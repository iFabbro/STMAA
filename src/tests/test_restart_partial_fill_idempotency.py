"""Restart idempotency for sequential partial fills on the same symbol."""
import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def executor_state_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    state_path = tmp_path / "state" / "broker_state.json"
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))
    return state_path


def _order(*, baseline, filled, reconciled):
    return {
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 10.0,
        "order_id": f"broker-order-{baseline}",
        "client_order_id": f"client-order-{baseline}",
        "status": "canceled",
        "filled_qty": filled,
        "pre_order_position_qty": baseline,
        "position_reconciled": reconciled,
    }


def _persist_sequential_partial_fills(executor):
    # The first order moved the position from 10 to 12 shares. A later order
    # started from that verified baseline and moved it from 12 to 15 shares.
    executor.state["order_submission_confirmed"] = {
        "client-order-first": _order(baseline=10.0, filled=2.0, reconciled=True),
        "client-order-second": _order(baseline=12.0, filled=3.0, reconciled=False),
    }
    executor._save_state()


def test_restart_reconciles_only_unreconciled_partial_fill_and_is_idempotent(
    executor_state_path, monkeypatch
):
    first_process = BrokerExecutor(paper_trading=True)
    _persist_sequential_partial_fills(first_process)

    restarted = BrokerExecutor(paper_trading=True)
    position_reads = []

    def read_positions():
        position_reads.append(True)
        return {"AAPL": {"qty": 15.0}}

    monkeypatch.setattr(restarted, "get_positions", read_positions)

    restarted._reconcile_terminal_order_positions()

    orders = restarted.state["order_submission_confirmed"]
    assert orders["client-order-first"]["position_reconciled"] is True
    assert orders["client-order-second"]["position_reconciled"] is True
    assert len(position_reads) == 1

    # A repeated reconciliation after restart must not add the fills again or
    # require another position read once both persisted records are reconciled.
    restarted._save_state()
    second_restart = BrokerExecutor(paper_trading=True)
    monkeypatch.setattr(
        second_restart,
        "get_positions",
        lambda: pytest.fail("Fully reconciled orders must not be processed again"),
    )
    second_restart._reconcile_terminal_order_positions()

    assert second_restart.state["order_submission_confirmed"][
        "client-order-first"
    ]["filled_qty"] == 2.0
    assert second_restart.state["order_submission_confirmed"][
        "client-order-second"
    ]["filled_qty"] == 3.0
    assert second_restart.state["order_submission_confirmed"][
        "client-order-first"
    ]["position_reconciled"] is True
    assert second_restart.state["order_submission_confirmed"][
        "client-order-second"
    ]["position_reconciled"] is True


def test_restart_keeps_partial_fill_blocked_when_position_snapshot_is_stale(
    executor_state_path, monkeypatch
):
    first_process = BrokerExecutor(paper_trading=True)
    _persist_sequential_partial_fills(first_process)

    restarted = BrokerExecutor(paper_trading=True)
    monkeypatch.setattr(
        restarted,
        "get_positions",
        lambda: {"AAPL": {"qty": 12.0}},
    )

    with pytest.raises(
        RuntimeError, match="position snapshot has not reconciled terminal order"
    ):
        restarted._reconcile_terminal_order_positions()

    orders = restarted.state["order_submission_confirmed"]
    assert orders["client-order-first"]["position_reconciled"] is True
    assert orders["client-order-second"].get("position_reconciled") is not True
