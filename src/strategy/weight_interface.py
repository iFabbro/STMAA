"""Weight-centric strategy interface.

PortfolioWeights preserves the weights emitted by the final pipeline stage.
In particular, it must not normalize after a risk overlay, because that can
undo VIX scaling or violate per-position limits.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


class StrategyComponent(Enum):
    """Pipeline components."""
    STOCK_SELECTION = "selection"
    PORTFOLIO_ALLOCATION = "allocation"
    TIMING_ADJUSTMENT = "timing"
    RISK_OVERLAY = "risk"


@dataclass
class PortfolioWeights:
    """Portfolio weight vector; preserves the supplied allocation exactly."""
    weights: np.ndarray
    tickers: List[str]
    timestamp: Optional[str] = None
    metadata: Dict = None

    def __post_init__(self):
        self.weights = np.asarray(self.weights, dtype=float)
        if self.weights.ndim != 1:
            raise ValueError("weights must be a one-dimensional vector")
        if len(self.weights) != len(self.tickers):
            raise ValueError(
                f"Mismatch: {len(self.weights)} weights vs {len(self.tickers)} tickers"
            )
        if not self.tickers or len(set(self.tickers)) != len(self.tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        if not np.isfinite(self.weights).all():
            raise ValueError("weights must all be finite")

    def to_dict(self) -> Dict:
        return {
            "weights": self.weights.tolist(),
            "tickers": self.tickers,
            "timestamp": self.timestamp,
            "metadata": self.metadata or {},
        }

    def get_exposure(self) -> float:
        """Return total absolute exposure, including long and short positions."""
        return float(np.abs(self.weights).sum())


class WeightCentricPipeline:
    """Pipeline: Selection -> Allocation -> Timing -> Risk."""

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.current_portfolio_value = initial_capital
        self.selection_module = None
        self.allocation_module = None
        self.timing_module = None
        self.risk_module = None

    def set_selection(self, module):
        self.selection_module = module
        return self

    def set_allocation(self, module):
        self.allocation_module = module
        return self

    def set_timing(self, module):
        self.timing_module = module
        return self

    def set_risk(self, module):
        self.risk_module = module
        return self

    def execute(self, market_data: pd.DataFrame, vix: Optional[float] = None,
                **kwargs) -> PortfolioWeights:
        """Run selection, allocation, timing, and risk in order.

        Risk modules declaring requires_verified_portfolio_value fail closed
        unless the caller supplies current account equity from a trusted source.
        """
        if getattr(self.risk_module, "requires_verified_portfolio_value", False):
            if "portfolio_value" not in kwargs:
                raise ValueError(
                    "portfolio_value is required for the risk overlay drawdown circuit breaker"
                )
            try:
                verified_value = float(kwargs["portfolio_value"])
            except (TypeError, ValueError) as exc:
                raise ValueError("portfolio_value must be finite and positive") from exc
            if not np.isfinite(verified_value) or verified_value <= 0:
                raise ValueError("portfolio_value must be finite and positive")
            self.current_portfolio_value = verified_value

        tickers = market_data.columns.get_level_values(0).unique().tolist()
        if self.selection_module:
            candidate_set = self.selection_module.select(market_data, tickers, **kwargs)
        else:
            candidate_set = tickers

        if self.allocation_module:
            w_base = self.allocation_module.allocate(
                market_data, candidate_set, **kwargs
            )
        else:
            if not candidate_set:
                raise ValueError("No tickers selected for allocation")
            w_base = np.ones(len(candidate_set), dtype=float) / len(candidate_set)

        if self.timing_module:
            w_timing = self.timing_module.adjust(
                market_data, w_base, candidate_set, **kwargs
            )
        else:
            w_timing = np.asarray(w_base, dtype=float).copy()

        if self.risk_module:
            w_final = self.risk_module.apply(
                market_data, w_timing, candidate_set, vix=vix, **kwargs
            )
        else:
            w_final = w_timing.copy()

        return PortfolioWeights(
            weights=w_final,
            tickers=candidate_set,
            timestamp=str(datetime.now()),
            metadata={"portfolio_value": self.current_portfolio_value},
        )
