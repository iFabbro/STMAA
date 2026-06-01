"""Risk Overlay Module - VIX-based volatility scaling"""
import numpy as np
import pandas as pd
from typing import List, Optional

class RiskOverlayModule:
    """Risk overlay per portfolio-level risk management"""
    
    def __init__(
        self,
        max_position_size: float = 0.2,
        max_drawdown_threshold: float = 0.15,
        vix_threshold_high: float = 30,
        vix_threshold_low: float = 15,
        initial_capital: float = 100000.0
    ):
        self.max_position_size = max_position_size
        self.max_drawdown_threshold = max_drawdown_threshold
        self.vix_threshold_high = vix_threshold_high
        self.vix_threshold_low = vix_threshold_low
        self.initial_capital = initial_capital
        self.current_portfolio_value = initial_capital
        self.peak_portfolio_value = initial_capital
    
    def apply(
        self,
        market_data: pd.DataFrame,
        weights: np.ndarray,
        tickers: List[str],
        vix: Optional[float] = None,
        **kwargs
    ) -> np.ndarray:
        """Applica risk overlay"""
        
        # 1. Cap position size
        weights = np.clip(weights, -self.max_position_size, self.max_position_size)
        
        # 2. VIX-based volatility scaling
        if vix is not None:
            exposure_scale = self._calculate_vix_scaling(vix)
            weights = weights * exposure_scale
            print(f"📉 VIX scaling: {exposure_scale:.2f} (VIX={vix:.1f})")
        
        # 3. Drawdown check
        if 'portfolio_value' in kwargs:
            self.current_portfolio_value = kwargs['portfolio_value']
            self.peak_portfolio_value = max(self.peak_portfolio_value, self.current_portfolio_value)
            
            drawdown = (self.peak_portfolio_value - self.current_portfolio_value) / self.peak_portfolio_value
            
            if drawdown > self.max_drawdown_threshold:
                print(f"🚨 CIRCUIT BREAKER: Drawdown {drawdown:.2%} > threshold {self.max_drawdown_threshold:.2%}")
                return np.zeros(len(tickers))
        
        # 4. Normalizza pesi
        if weights.sum() != 0:
            weights = weights / weights.sum()
        
        return weights
    
    def _calculate_vix_scaling(self, vix: float) -> float:
        """Calcola scaling factor basato su VIX"""
        
        if vix < self.vix_threshold_low:
            return 1.0
        elif vix > self.vix_threshold_high:
            return 0.5
        else:
            scale = 1.0 - (vix - self.vix_threshold_low) / (self.vix_threshold_high - self.vix_threshold_low) * 0.5
            return max(0.5, scale)
    
    def adaptive_position_sizing(
        self,
        weights: np.ndarray,
        atr_values: np.ndarray,
        target_risk: float = 0.02
    ) -> np.ndarray:
        """Position sizing adattivo basato su volatilità (ATR)"""
        
        volatility_normalized = 1 / (atr_values + 1e-8)
        risk_adjusted_weights = weights * volatility_normalized
        
        if risk_adjusted_weights.sum() != 0:
            risk_adjusted_weights = risk_adjusted_weights / risk_adjusted_weights.sum()
        
        return risk_adjusted_weights
