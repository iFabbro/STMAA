"""Backtest Engine execution-consistent con live trading"""
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Callable
from pathlib import Path
from datetime import datetime

class ExecutionConsistentBacktest:
    """Backtest con stessa semantica esecutiva del live"""
    
    def __init__(
        self,
        initial_capital: float = 100000.0,
        transaction_cost: float = 0.001,
        slippage: float = 0.0005
    ):
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost
        self.slippage = slippage
    
    def run_backtest(
        self,
        price_data: pd.DataFrame,
        weights_generator: Callable,
        tickers: List[str]
    ) -> Dict:
        """Esegue backtest execution-consistent"""
        print("📊 Avvio backtest execution-consistent...")
        
        portfolio_values = []
        current_weights = np.zeros(len(tickers))
        portfolio_value = self.initial_capital
        
        for timestamp, row in price_data.iterrows():
            pw = weights_generator(timestamp, row)
            target_weights = pw.weights if hasattr(pw, 'weights') else pw
            
            turnover = np.abs(target_weights - current_weights).sum()
            cost = turnover * self.transaction_cost
            
            target_weights = target_weights * (1 - self.slippage)
            
            close_cols = [f'Close_{t}' for t in tickers if f'Close_{t}' in row.index]
            if len(close_cols) == len(tickers):
                returns = pd.Series([row[col] for col in close_cols]).pct_change().fillna(0)
                step_return = np.dot(target_weights[:len(returns)], returns.values)
                
                portfolio_value *= (1 + step_return - cost)
                current_weights = target_weights.copy()
            
            portfolio_values.append(portfolio_value)
        
        portfolio_series = pd.Series(portfolio_values, index=price_data.index)
        
        metrics = self._calculate_metrics(portfolio_series)
        
        print(f"✅ Backtest completato. Valore finale: ${portfolio_value:.2f}")
        
        return {
            'portfolio_values': portfolio_series,
            'metrics': metrics,
            'final_value': portfolio_value
        }
    
    def _calculate_metrics(self, portfolio_series: pd.Series) -> Dict:
        """Calcola metrics standard FinRL-X"""
        returns = portfolio_series.pct_change().fillna(0)
        
        cumulative_return = (portfolio_series.iloc[-1] / portfolio_series.iloc[0]) - 1
        annualized_return = (1 + cumulative_return) ** (252 / len(portfolio_series)) - 1
        annualized_vol = returns.std() * np.sqrt(252)
        sharpe_ratio = annualized_return / annualized_vol if annualized_vol > 0 else 0
        
        rolling_max = portfolio_series.cummax()
        drawdown = (portfolio_series - rolling_max) / rolling_max
        max_drawdown = drawdown.min()
        
        downside_returns = returns[returns < 0]
        downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0
        sortino_ratio = annualized_return / downside_vol if downside_vol > 0 else 0
        
        calmar_ratio = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0
        
        return {
            'cumulative_return': cumulative_return,
            'annualized_return': annualized_return,
            'annualized_volatility': annualized_vol,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'calmar_ratio': calmar_ratio,
            'max_drawdown': max_drawdown
        }
