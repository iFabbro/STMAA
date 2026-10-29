"""Restart persistence for cumulative terminal partial-fill reconciliation."""
from datetime import datetime, timezone

from src.execution.broker_executor import BrokerExecutor


def _order(*, baseline, filled, timestamp):
    return {
        "ticker": "BTC/USD",
        "side": "buy",
        "quantity": 10.0,
        "order_id": f"broker-order-{timestamp}",
        "client_order_id": f"client-order-{timestamp}",
        "status": "canceled",
        "filled_qty": filled,
        "pre_order_position_qty": baseline,
        "timestamp": timestamp,
        "position_reconciled": False,
    }


def _executor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    return BrokerExecutor(paper_trading=True)


def test_cumulative_terminal_fills_survive_restart_and_are_not_reprocessed(
    monkeypatch, tmp_path
):
    first = _executor(monkeypatch, tmp_path)
    first.state["order_submission_confirmed"] = {
        "client-order-1": _order(
            baseline=0.0,
            filled=0.002,
            timestamp="2026-10-28T10:00:00+00:00",
        ),
        "client-order-2": _order(
            baseline=0.002,
            filled=0.003,
            timestamp="2026-10-28T10:01:00+00:00",
        ),
    }
    first._save_state()

    restarted = _executor(monkeypatch, tmp_path)
    position_reads = []
    monkeypatch.setattr(
        restarted,
        "get_positions",
        lambda: position_reads.append(True) or {"BTC/USD": {"qty": 0.005}},
    )
    restarted._reconcile_terminal_order_positions()

    reconciled = restarted.state["order_submission_confirmed"]
    assert reconciled["client-order-1"]["position_reconciled"] is True
    assert reconciled["client-order-2"]["position_reconciled"] is True
    assert len(position_reads) == 1

    final_restart = _executor(monkeypatch, tmp_path)

    def unexpected_position_read():
        raise AssertionError(
            "Already-reconciled terminal orders must not be processed again"
        )

    monkeypatch.setattr(final_restart, "get_positions", unexpected_position_read)
    final_restart._reconcile_terminal_order_positions()

    persisted = final_restart.state["order_submission_confirmed"]
    assert persisted["client-order-1"]["position_reconciled"] is True
    assert persisted["client-order-2"]["position_reconciled"] is True
