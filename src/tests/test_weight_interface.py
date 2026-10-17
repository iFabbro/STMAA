import numpy as np
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.strategy.weight_interface import PortfolioWeights, WeightCentricPipeline

def test_portfolio_weights_creation():
    """Test creazione vettore pesi"""
    weights = PortfolioWeights(
        weights=np.array([0.4, 0.3, 0.3]),
        tickers=['AAPL', 'GOOGL', 'MSFT']
    )
    
    assert np.isclose(weights.weights.sum(), 1.0)
    assert len(weights.tickers) == 3
    print("✅ PortfolioWeights creato correttamente")

def test_portfolio_weights_normalization():
    """Test normalizzazione automatica pesi"""
    weights = PortfolioWeights(
        weights=np.array([40, 30, 30]),  # Non normalizzati
        tickers=['AAPL', 'GOOGL', 'MSFT']
    )
    
    assert np.isclose(weights.weights.sum(), 1.0)
    assert np.isclose(weights.weights[0], 0.4)
    print("✅ Normalizzazione pesi funzionante")

def test_pipeline_creation():
    """Test creazione pipeline"""
    pipeline = WeightCentricPipeline(initial_capital=100000)
    assert pipeline.initial_capital == 100000
    print("✅ Pipeline creata correttamente")

if __name__ == "__main__":
    test_portfolio_weights_creation()
    test_portfolio_weights_normalization()
    test_pipeline_creation()
    print("\n🎉 Tutti i test weight interface passati!")
