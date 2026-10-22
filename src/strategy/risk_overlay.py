"""Risk overlay with per-position and gross-exposure limits."""
from typing import List, Optional
import numpy as np
import pandas as pd


class RiskOverlayModule:
    """Apply risk controls without re-leveraging normalized weights."""

    def __init__(self, max_position_size: float = 0.2,
                 max_gross_exposure: float = 1.0,
                 max_drawdown_threshold: float = 0.15,
                 vix_threshold_high: float = 30,
                 vix_threshold_low: float = 15,
                 initial_capital: float = 100000.0):
        parameters = np.asarray([
            max_position_size, max_gross_exposure, max_drawdown_threshold,
            vix_threshold_high, vix_threshold_low, initial_capital,
        ], dtype=float)
        if not np.isfinite(parameters).all():
            raise ValueError("Risk parameters must be finite")
        if max_position_size <= 0:
            raise ValueError("max_position_size must be positive")
        if max_gross_exposure <= 0:
            raise ValueError("max_gross_exposure must be positive")
        if not 0 <= max_drawdown_threshold < 1:
            raise ValueError("max_drawdown_threshold must be in [0, 1)")
        if vix_threshold_high <= vix_threshold_low:
            raise ValueError("vix_threshold_high must exceed vix_threshold_low")
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        self.max_position_size = float(max_position_size)
        self.max_gross_exposure = float(max_gross_exposure)
        self.max_drawdown_threshold = float(max_drawdown_threshold)
        self.vix_threshold_high = float(vix_threshold_high)
        self.vix_threshold_low = float(vix_threshold_low)
        self.initial_capital = float(initial_capital)
        self.current_portfolio_value = float(initial_capital)
        self.peak_portfolio_value = float(initial_capital)

    def apply(self, market_data: pd.DataFrame, weights: np.ndarray,
              tickers: List[str], vix: Optional[float] = None, **kwargs) -> np.ndarray:
        """Cap positions and gross exposure, then apply drawdown circuit breaker."""
        target = np.asarray(weights, dtype=float)
        if target.shape != (len(tickers),):
            raise ValueError(
                f"Expected {len(tickers)} weights for the supplied tickers; "
                f"received shape {target.shape}"
            )
        if not np.isfinite(target).all():
            raise ValueError("Weights must all be finite")
        target = np.clip(target, -self.max_position_size, self.max_position_size)
        if vix is not None:
            vix_value = float(vix)
            if not np.isfinite(vix_value) or vix_value < 0:
                raise ValueError("vix must be a finite, non-negative number")
            target = target * self._calculate_vix_scaling(vix_value)

        gross_exposure = float(np.abs(target).sum())
        if gross_exposure > self.max_gross_exposure:
            target *= self.max_gross_exposure / gross_exposure

        if "portfolio_value" in kwargs:
            portfolio_value = float(kwargs["portfolio_value"])
            if not np.isfinite(portfolio_value) or portfolio_value <= 0:
                raise ValueError("portfolio_value must be finite and positive")
            self.current_portfolio_value = portfolio_value
            self.peak_portfolio_value = max(self.peak_portfolio_value, portfolio_value)
            drawdown = (
                self.peak_portfolio_value - portfolio_value
            ) / self.peak_portfolio_value
            if drawdown >= self.max_drawdown_threshold:
                return np.zeros(len(tickers), dtype=float)
        return target

    def _calculate_vix_scaling(self, vix: float) -> float:
        """Return a scale in [0.5, 1.0] based on VIX."""
        if not np.isfinite(vix) or vix < 0:
            raise ValueError("vix must be a finite, non-negative number")
        if vix < self.vix_threshold_low:
            return 1.0
        if vix > self.vix_threshold_high:
            return 0.5
        scale = 1.0 - (
            (vix - self.vix_threshold_low)
            / (self.vix_threshold_high - self.vix_threshold_low)
        ) * 0.5
        return float(np.clip(scale, 0.5, 1.0))

    def adaptive_position_sizing(
        self, weights: np.ndarray, atr_values: np.ndarray
    ) -> np.ndarray:
        """Adjust signed allocation preferences inversely to normalized ATR.

        ATR values must be normalized fractions (ATR / price). This is a
        relative volatility adjustment, not a calibrated loss limit.
        """
        raw_weights = np.asarray(weights, dtype=float)
        atr = np.asarray(atr_values, dtype=float)
        if raw_weights.ndim != 1 or atr.shape != raw_weights.shape:
            raise ValueError("weights and atr_values must be equal-length vectors")
        if not np.isfinite(raw_weights).all():
            raise ValueError("weights must all be finite")
        if not np.isfinite(atr).all() or (atr <= 0).any():
            raise ValueError("normalized ATR values must be finite and strictly positive")
        adjusted = raw_weights / atr
        max_abs = float(np.max(np.abs(adjusted))) if adjusted.size else 0.0
        if max_abs > self.max_position_size:
            adjusted = adjusted * (self.max_position_size / max_abs)
        return adjusted
