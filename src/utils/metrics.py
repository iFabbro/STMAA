"""Metriche avanzate per trading: drawdown, Sharpe, exposure, regime detection."""
import numpy as np
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime


def calculate_max_drawdown(equity_curve: List[float]) -> float:
    """Calcola max drawdown (%)."""
    if not equity_curve or len(equity_curve) < 2:
        return 0.0
    
    equity = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    
    return float(np.min(drawdown))


def calculate_sharpe_ratio(returns: List[float], risk_free_rate: float = 0.02) -> float:
    """Calcola Sharpe Ratio (annualizzato, daily returns)."""
    if not returns or len(returns) < 2:
        return 0.0
    
    returns_array = np.array(returns)
    if np.std(returns_array) == 0:
        return 0.0
    
    excess_returns = returns_array - risk_free_rate / 252  # daily risk-free
    sharpe = np.mean(excess_returns) / np.std(returns_array) * np.sqrt(252)
    
    return float(sharpe)


def calculate_exposure(positions: Dict[str, Dict], portfolio_value: float) -> float:
    """Calcola esposizione (% portafoglio esposta)."""
    if portfolio_value <= 0:
        return 0.0
    
    total_position_value = sum(p.get('market_value', 0) for p in positions.values())
    exposure = total_position_value / portfolio_value
    
    return float(exposure)


def detect_regime(returns: List[float], window: int = 20) -> str:
    """Rileva regime di mercato basato su rolling returns.
    
    Returns:
        'bull' (rialzista), 'bear' (ribassista), 'sideways' (laterale)
    """
    if not returns or len(returns) < window:
        return 'sideways'
    
    returns_array = np.array(returns[-window:])
    mean_return = np.mean(returns_array)
    std_return = np.std(returns_array)
    
    # Regime basato su mean e volatilità
    if mean_return > 0.0005:  # > 0.05% daily average
        return 'bull'
    elif mean_return < -0.0005:  # < -0.05% daily average
        return 'bear'
    else:
        return 'sideways'


def calculate_sortino_ratio(returns: List[float], risk_free_rate: float = 0.02) -> float:
    """Calcola Sortino Ratio (solo downside deviation)."""
    if not returns or len(returns) < 2:
        return 0.0
    
    returns_array = np.array(returns)
    downside_returns = returns_array[returns_array < 0]
    
    if len(downside_returns) == 0:
        return float('inf') if np.mean(returns_array) > 0 else 0.0
    
    excess_returns = np.mean(returns_array) - risk_free_rate / 252
    downside_std = np.std(downside_returns)
    
    if downside_std == 0:
        return 0.0
    
    sortino = excess_returns / downside_std * np.sqrt(252)
    return float(sortino)


def calculate_calmar_ratio(returns: List[float], equity_curve: List[float]) -> float:
    """Calcola Calmar Ratio (annualized return / max drawdown)."""
    if not returns or len(returns) < 252:  # bisogno di almeno 1 anno
        return 0.0
    
    annual_return = np.mean(returns) * 252
    max_dd = abs(calculate_max_drawdown(equity_curve))
    
    if max_dd == 0:
        return 0.0
    
    return float(annual_return / max_dd)


class PortfolioMetrics:
    """Calcola e memorizza metriche portfolio."""
    
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.equity_curve = [initial_capital]
        self.returns = []
        self.positions = {}
        self.portfolio_value = initial_capital
        
    def update(self, portfolio_value: float, positions: Dict[str, Dict]):
        """Aggiorna metriche con nuovo stato portfolio."""
        self.equity_curve.append(portfolio_value)
        
        if len(self.equity_curve) >= 2:
            ret = (portfolio_value - self.equity_curve[-2]) / self.equity_curve[-2]
            self.returns.append(ret)
        
        self.positions = positions
        self.portfolio_value = portfolio_value
    
    def get_all_metrics(self) -> Dict:
        """Calcola tutte le metriche."""
        drawdown = calculate_max_drawdown(self.equity_curve)
        sharpe = calculate_sharpe_ratio(self.returns)
        exposure = calculate_exposure(self.positions, self.portfolio_value)
        regime = detect_regime(self.returns)
        sortino = calculate_sortino_ratio(self.returns)
        calmar = calculate_calmar_ratio(self.returns, self.equity_curve)
        
        # Additional metrics
        total_days = len(self.equity_curve) - 1
        total_return = (self.portfolio_value - self.initial_capital) / self.initial_capital
        annualized_return = ((self.portfolio_value / self.initial_capital) ** (252 / max(total_days, 1)) - 1) if total_days > 0 else 0
        
        return {
            'portfolio_value': self.portfolio_value,
            'initial_capital': self.initial_capital,
            'total_return': float(total_return),
            'annualized_return': float(annualized_return),
            'max_drawdown': drawdown,
            'sharpe_ratio': sharpe,
            'sortino_ratio': sortino,
            'calmar_ratio': calmar,
            'exposure': exposure,
            'regime': regime,
            'equity_curve': self.equity_curve,
            'returns': self.returns,
            'positions_count': len(self.positions),
        }
