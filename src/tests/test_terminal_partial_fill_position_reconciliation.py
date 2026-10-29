"""Terminal partial-fill reconciliation must use only verified filled quantity."""
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


def _terminal_partial_order(side, baseline, filled):
    return {
        "ticker": "AAPL",
        "side": side,
        "quantity": 10.0,
        "order_id": "broker-partial-order",
        "client_order_id": "client-partial-order",
        "status": "canceled",
        "filled_qty": filled,
        "pre_order_position_qty": baseline,
        "position_reconciled": False,
    }


@pytest.mark.parametrize(
    ("side", "baseline", "filled", "actual"),
    [
        pytest.param("buy", 10.0, 4.0, 14.0, id="partially-filled-buy-then-canceled"),
        pytest.param("sell", 10.0, 3.0, 7.0, id="partially-filled-sell-then-canceled"),
    ],
)
def test_terminal_partial_fill_reconciles_baseline_plus_or_minus_filled_quantity(
    executor_env, monkeypatch, side, baseline, filled, actual
):
    executor = executor_env
    executor.state["order_submission_confirmed"] = {
        "client-partial-order": _terminal_partial_order(side, baseline, filled)
    }
    executor._save_state()
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {"AAPL": {"qty": actual}},
    )

    executor._reconcile_terminal_order_positions()

    stored = executor.state["order_submission_confirmed"]["client-partial-order"]
    assert stored["position_reconciled"] is True


def test_terminal_partial_fill_with_position_including_wrong_quantity_fails_closed(
    executor_env, monkeypatch
):
    executor = executor_env
    executor.state["order_submission_confirmed"] = {
        "client-partial-order": _terminal_partial_order("buy", 10.0, 4.0)
    }
    executor._save_state()
    # The position snapshot still has the baseline, so the four-share fill
    # cannot be proven reflected in the broker position.
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {"AAPL": {"qty": 10.0}},
    )

    with pytest.raises(
        RuntimeError,
        match="position snapshot has not reconciled terminal order",
    ):
        executor._reconcile_terminal_order_positions()

    stored = executor.state["order_submission_confirmed"]["client-partial-order"]
    assert stored.get("position_reconciled") is not True
