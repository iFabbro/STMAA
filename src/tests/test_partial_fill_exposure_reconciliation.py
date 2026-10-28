"""Partial-fill exposure integrity tests for the residual STMAA audit."""
from types import SimpleNamespace

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def executor_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    return BrokerExecutor(paper_trading=True)


def test_partial_fill_exposure_reserves_full_order_notional_conservatively(
    executor_env,
):
    executor = executor_env
    order = SimpleNamespace(
        id="partial-aapl-1",
        symbol="AAPL",
        status="partially_filled",
        qty="10",
        filled_qty="4",
        limit_price="100",
        notional=None,
    )

    # The current policy reserves the original quantity because the broker's
    # position snapshot may lag fills. This prevents undercounting, but can
    # temporarily overstate exposure when positions already include the fill.
    assert executor._estimate_order_notional(order, prices={"AAPL": 100.0}) == 1000.0


@pytest.mark.parametrize(
    "order",
    [
        pytest.param(
            SimpleNamespace(
                id="filled-over-qty",
                symbol="AAPL",
                status="partially_filled",
                qty="3",
                filled_qty="4",
                limit_price="100",
                notional=None,
            ),
            id="filled-quantity-exceeds-requested",
        ),
        pytest.param(
            SimpleNamespace(
                id="partial-no-qty",
                symbol="AAPL",
                status="partially_filled",
                qty=None,
                filled_qty="1",
                limit_price="100",
                notional=None,
            ),
            id="filled-quantity-without-requested-quantity",
        ),
        pytest.param(
            SimpleNamespace(
                id="active-no-remainder",
                symbol="AAPL",
                status="partially_filled",
                qty="4",
                filled_qty="4",
                limit_price="100",
                notional=None,
            ),
            id="nonterminal-status-with-zero-remainder",
        ),
    ],
)
def test_inconsistent_partial_fill_snapshot_fails_closed(executor_env, order):
    with pytest.raises(RuntimeError, match="Cannot estimate exposure"):
        executor_env._estimate_order_notional(order, prices={"AAPL": 100.0})
