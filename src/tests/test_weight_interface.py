import numpy as np
import pandas as pd
import pytest

from src.strategy.risk_overlay import RiskOverlayModule
from src.strategy.weight_interface import PortfolioWeights, WeightCentricPipeline


def test_portfolio_weights_creation():
    weights = PortfolioWeights(
        weights=np.array([0.4, 0.3, 0.3]),
        tickers=["AAPL", "GOOGL", "MSFT"],
    )
    np.testing.assert_allclose(weights.weights, [0.4, 0.3, 0.3])
    assert len(weights.tickers) == 3


def test_portfolio_weights_preserve_risk_overlay_output():
    # The overlay intentionally reduces total exposure; the interface must not
    # normalize it back to 100% after risk controls have run.
    overlay = RiskOverlayModule(max_position_size=0.2)
    risk_adjusted = overlay.apply(
        None, np.array([0.8, 0.8, -0.7]), ["A", "B", "C"], vix=35
    )
    weights = PortfolioWeights(weights=risk_adjusted, tickers=["A", "B", "C"])
    np.testing.assert_allclose(weights.weights, [0.1, 0.1, -0.1])
    assert np.max(np.abs(weights.weights)) <= 0.2
    assert weights.get_exposure() == pytest.approx(0.3)


def test_portfolio_weights_reject_invalid_contract():
    with pytest.raises(ValueError, match="finite"):
        PortfolioWeights(weights=np.array([np.nan]), tickers=["A"])
    with pytest.raises(ValueError, match="Mismatch"):
        PortfolioWeights(weights=np.array([0.1, 0.2]), tickers=["A"])


def test_pipeline_creation():
    pipeline = WeightCentricPipeline(initial_capital=100000)
    assert pipeline.initial_capital == 100000



def _market_data_for_pipeline():
    columns = pd.MultiIndex.from_tuples([("A", "Close"), ("B", "Close")])
    return pd.DataFrame([[100.0, 100.0]], columns=columns)


def test_risk_pipeline_requires_verified_portfolio_value():
    pipeline = WeightCentricPipeline().set_risk(RiskOverlayModule())
    with pytest.raises(ValueError, match="portfolio_value is required"):
        pipeline.execute(_market_data_for_pipeline())


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_risk_pipeline_rejects_invalid_portfolio_value(value):
    pipeline = WeightCentricPipeline().set_risk(RiskOverlayModule())
    with pytest.raises(ValueError, match="portfolio_value must be finite and positive"):
        pipeline.execute(_market_data_for_pipeline(), portfolio_value=value)


def test_risk_pipeline_passes_verified_equity_to_drawdown_circuit_breaker():
    pipeline = WeightCentricPipeline().set_risk(RiskOverlayModule())
    result = pipeline.execute(
        _market_data_for_pipeline(),
        portfolio_value=80000.0,
        peak_portfolio_value=100000.0,
    )
    np.testing.assert_array_equal(result.weights, np.zeros(2))
    assert result.metadata["portfolio_value"] == pytest.approx(80000.0)
