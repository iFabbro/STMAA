"""Weight-Centric Interface - cuore architettura FinRL-X

Ogni strategia produce un vettore di pesi w_t ∈ R^n
Questo è l'UNICO contratto tra strategia ed esecuzione.
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

class StrategyComponent(Enum):
    """Componenti modulari della pipeline"""
    STOCK_SELECTION = "selection"
    PORTFOLIO_ALLOCATION = "allocation"
    TIMING_ADJUSTMENT = "timing"
    RISK_OVERLAY = "risk"

@dataclass
class PortfolioWeights:
    """Vettore di pesi di portafoglio - interface contract"""
    weights: np.ndarray  # Pesi normalizzati (somma = 1.0)
    tickers: List[str]   # Lista tickers corrispondente
    timestamp: Optional[str] = None
    metadata: Dict = None
    
    def __post_init__(self):
        # Normalizza pesi
        if self.weights.sum() > 0:
            self.weights = self.weights / self.weights.sum()
        
        # Verifica lunghezza
        assert len(self.weights) == len(self.tickers), \
            f"Mismatch: {len(self.weights)} weights vs {len(self.tickers)} tickers"
    
    def to_dict(self) -> Dict:
        return {
            'weights': self.weights.tolist(),
            'tickers': self.tickers,
            'timestamp': self.timestamp,
            'metadata': self.metadata or {}
        }
    
    def get_exposure(self) -> float:
        """Restituisce esposizione totale (assoluta)"""
        return np.abs(self.weights).sum()

class WeightCentricPipeline:
    """Pipeline composita FinRL-X: Selection → Allocation → Timing → Risk"""
    
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.current_portfolio_value = initial_capital
        self.selection_module = None
        self.allocation_module = None
        self.timing_module = None
        self.risk_module = None
    
    def set_selection(self, module):
        """Imposta modulo stock selection"""
        self.selection_module = module
        return self
    
    def set_allocation(self, module):
        """Imposta modulo portfolio allocation"""
        self.allocation_module = module
        return self
    
    def set_timing(self, module):
        """Imposta modulo timing adjustment"""
        self.timing_module = module
        return self
    
    def set_risk(self, module):
        """Imposta modulo risk overlay"""
        self.risk_module = module
        return self
    
    def execute(self, market_data: pd.DataFrame, 
                vix: Optional[float] = None,
                **kwargs) -> PortfolioWeights:
        """Esegue pipeline completa secondo Algorithm 1
        
        w_t = R_t(T_t(A_t(S_t(X≤t))))
        """
        tickers = market_data.columns.get_level_values(0).unique().tolist()
        
        # 1. Stock Selection: seleziona candidato C_t ⊆ U_t
        if self.selection_module:
            candidate_set = self.selection_module.select(market_data, tickers, **kwargs)
        else:
            candidate_set = tickers  # Default: tutti i tickers
        
        # 2. Portfolio Allocation: w_base_t = A(C_t)
        if self.allocation_module:
            w_base = self.allocation_module.allocate(market_data, candidate_set, **kwargs)
        else:
            w_base = np.ones(len(candidate_set)) / len(candidate_set)  # Equal-weight
        
        # 3. Timing Adjustment: w_timing_t = T(w_base_t)
        if self.timing_module:
            w_timing = self.timing_module.adjust(market_data, w_base, candidate_set, **kwargs)
        else:
            w_timing = w_base.copy()
        
        # 4. Risk Overlay: w_t = R(w_timing_t)
        if self.risk_module:
            w_final = self.risk_module.apply(market_data, w_timing, candidate_set, vix=vix, **kwargs)
        else:
            w_final = w_timing.copy()
        
        return PortfolioWeights(
            weights=w_final,
            tickers=candidate_set,
            timestamp=str(datetime.now()),
            metadata={'portfolio_value': self.current_portfolio_value}
        )
