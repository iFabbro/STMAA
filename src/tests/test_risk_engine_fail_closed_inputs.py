"""Risk sizing must fail closed when any exposure input is unreliable."""
import numpy as np
import pytest

from src.execution.broker_executor import RiskEngine


@pytest.mark.parametrize(
    ("target_notional", "portfolio_value", "positions", "reserved_notional"),
    [
        pytest.param(float("nan"), 10_000.0, {}, 0.0, id="nan-target"),
        pytest.param(float("inf"), 10_000.0, {}, 0.0, id="infinite-target"),
        pytest.param(1_000.0, float("nan"), {}, 0.0, id="nan-equity"),
        pytest.param(1_000.0, float("inf"), {}, 0.0, id="infinite-equity"),
        pytest.param(1_000.0, 10_000.0, {}, float("nan"), id="nan-reserve"),
        pytest.param(1_000.0, 10_000.0, {}, float("inf"), id="infinite-reserve"),
        pytest.param(1_000.0, 10_000.0, {}, -1.0, id="negative-reserve"),
        pytest.param(
            1_000.0,
            10_000.0,
            {"MSFT": {"market_value": float("nan")}},
            0.0,
            id="nan-held-exposure",
        ),
        pytest.param(
            1_000.0,
            10_000.0,
            {"MSFT": {"market_value": float("inf")}},
            0.0,
            id="infinite-held-exposure",
        ),
    ],
)
def test_risk_engine_rejects_unreliable_numeric_inputs(
    target_notional, portfolio_value, positions, reserved_notional
):
    engine = RiskEngine()

    with pytest.raises(ValueError):
        engine.allow(
            "AAPL",
            target_notional,
            portfolio_value,
            positions,
            reserved_notional=reserved_notional,
        )
