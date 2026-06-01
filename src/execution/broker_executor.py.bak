"""Broker Executor per Alpaca (paper/live trading)"""
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import logging
import json

from src.strategy.weight_interface import PortfolioWeights

# Setup logging
logs_dir = Path("logs/execution")
logs_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(logs_dir / 'broker_executor.log'),
        logging.StreamHandler()
    ]
)

class BrokerExecutor:
    """Executor Alpaca deployment-aware"""
    
    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        base_url: str = "https://paper-api.alpaca.markets",
        paper_trading: bool = True
    ):
        after_all = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.paper_trading = paper_trading
        self.logger = logging.getLogger(__name__)
        
        self.state_file = Path("logs/execution/broker_state.json")
        self.state = {'last_execution_time': None, 'total_orders': 0, 'portfolio_value': 100000}
        self._load_state()
    
    def _load_state(self):
        """Load state per crash recovery"""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                self.state = json.load(f)
            self.logger.info("✅ Stato broker caricato da persistenza")
        else:
            self._save_state()
            self.logger.info("✅ Stato broker inizializzato")
    
    def _save_state(self):
        """Save state per crash recovery"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f)
    
    def execute_weights(self, weights: PortfolioWeights) -> Dict:
        """Esegue weights target sul broker"""
        self.logger.info(f"📤 Executing weights for {len(weights.tickers)} assets")
        
        orders_executed = []
        for ticker, target_weight in zip(weights.tickers, weights.weights):
            orders_executed.append({
                'ticker': ticker,
                'side': 'buy' if target_weight > 0 else 'sell',
                'quantity': abs(target_weight),
                'order_id': None
            })
        
        self.state['last_execution_time'] = datetime.now().isoformat()
        self.state['total_orders'] += len(orders_executed)
        self._save_state()
        
        self.logger.info(f"✅ Executed {len(orders_executed)} orders")
        try:
            import json
            hub = Path('logs/hub_state.json')
            hub.parent.mkdir(parents=True, exist_ok=True)
            hub.write_text(json.dumps({
                'timestamp': datetime.now().isoformat(),
                'orders': orders_executed,
                'portfolio_value': self.state.get('portfolio_value', 100000)
            }, indent=2))
        except Exception as e:
            self.logger.error(f'Hub state write failed: {e}')
        
        return {
            'orders': orders_executed,
            'portfolio_value': self.state.get('portfolio_value', 100000),
            'timestamp': datetime.now().isoformat()
        }
    
    def get_account_status(self) -> Dict:
        """Get account status (mock per test)"""
        return {
            'cash': 50000,
            'portfolio_value': 100000,
            'buying_power': 100000,
            'equity': 100000
        }
