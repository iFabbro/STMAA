import pytest

from src.execution.broker_executor import (
    RISK_LIMITS,
    PortfolioManager,
    RiskEngine,
    _validate_risk_limits,
)


def test_executor_defaults_match_shared_runtime_risk_configuration():
    assert RISK_LIMITS["max_portfolio_exposure"] == pytest.approx(0.50)
    assert RISK_LIMITS["max_position_pct"] == pytest.approx(0.20)
    assert RISK_LIMITS["max_drawdown"] == pytest.approx(0.15)


def test_risk_engine_uses_canonical_per_position_and_gross_exposure_defaults():
    engine = RiskEngine()
    assert engine.max_position_pct == pytest.approx(RISK_LIMITS["max_position_pct"])
    assert engine.max_exposure_pct == pytest.approx(RISK_LIMITS["max_portfolio_exposure"])
    allowed, reason = engine.allow("AAPL", 30_000, 100_000, {})
    assert allowed == pytest.approx(20_000)
    assert reason is None


def test_portfolio_manager_uses_same_gross_exposure_limit_as_risk_engine():
    manager = PortfolioManager()
    assert manager.max_total_exposure_pct == pytest.approx(
        RISK_LIMITS["max_portfolio_exposure"]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_position_pct": 0.60},
        {"max_position_pct": 0.0},
        {"max_portfolio_exposure": float("nan")},
        {"max_drawdown": 1.0},
    ],
)
def test_risk_limit_validation_rejects_invalid_or_conflicting_values(overrides):
    limits = dict(RISK_LIMITS)
    limits.update(overrides)
    with pytest.raises(ValueError):
        _validate_risk_limits(limits)


def test_runtime_risk_limits_can_be_explicitly_aligned_without_changing_other_caps():
    limits = {
        "max_portfolio_exposure": 0.40,
        "max_position_pct": 0.10,
        "max_drawdown": 0.12,
    }
    assert _validate_risk_limits(limits) is limits
    engine = RiskEngine(
        max_position_pct=limits["max_position_pct"],
        max_exposure_pct=limits["max_portfolio_exposure"],
    )
    assert engine.max_position_pct == pytest.approx(0.10)
    assert engine.max_exposure_pct == pytest.approx(0.40)
