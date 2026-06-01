import pytest
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights
import numpy as np

def test_executor_creation_structure():
    """Test struttura executor (senza API key reali)"""
    executor = BrokerExecutor(paper_trading=True)
    
    assert executor.paper_trading == True
    assert executor.state_file.exists()
    print("✅ BrokerExecutor strutturato correttamente (deployment-aware)")

def test_execute_weights_mock():
    """Test esecuzione weights (mock)"""
    executor = BrokerExecutor()
    
    weights = PortfolioWeights(
        weights=np.array([0.4, 0.3, 0.3]),
        tickers=['AAPL', 'GOOGL', 'MSFT']
    )
    
    result = executor.execute_weights(weights)
    
    assert 'orders' in result
    assert len(result['orders']) == 3
    assert result['timestamp'] is not None
    print("✅ Execution weights mock funzionante")

if __name__ == "__main__":
    test_executor_creation_structure()
    test_execute_weights_mock()
    print("\n🎉 Tutti i test broker executor passati!")
