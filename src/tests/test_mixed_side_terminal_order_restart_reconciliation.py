"""Restart reconciliation coverage for mixed-side terminal fills on one symbol."""
import pytest

from src.execution.broker_executor import BrokerExecutor


def _executor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    return BrokerExecutor(paper_trading=True)


def _order(*, side, baseline, filled, timestamp, client_id):
    return {
        "ticker": "AAPL",
        "side": side,
        "quantity": 10.0,
        "order_id": f"broker-{client_id}",
        "client_order_id": client_id,
        "status": "canceled",
        "filled_qty": filled,
        "pre_order_position_qty": baseline,
        "timestamp": timestamp,
        "position_reconciled": False,
    }


@pytest.mark.parametrize(
    ("first", "second", "final_qty"),
    [
        pytest.param(
            _order(
                side="buy",
                baseline=0.0,
                filled=5.0,
                timestamp="2026-11-01T10:00:00+00:00",
                client_id="buy-first",
            ),
            _order(
                side="sell",
                baseline=5.0,
                filled=2.0,
                timestamp="2026-11-01T10:01:00+00:00",
                client_id="sell-second",
            ),
            3.0,
            id="buy-then-sell",
        ),
        pytest.param(
            _order(
                side="sell",
                baseline=10.0,
                filled=4.0,
                timestamp="2026-11-01T10:00:00+00:00",
                client_id="sell-first",
            ),
            _order(
                side="buy",
                baseline=6.0,
                filled=3.0,
                timestamp="2026-11-01T10:01:00+00:00",
                client_id="buy-second",
            ),
            9.0,
            id="sell-then-buy",
        ),
    ],
)
def test_mixed_side_terminal_fills_reconcile_after_restart_and_remain_idempotent(
    monkeypatch, tmp_path, first, second, final_qty
):
    initial = _executor(monkeypatch, tmp_path)
    initial.state["order_submission_confirmed"] = {
        first["client_order_id"]: first,
        second["client_order_id"]: second,
    }
    initial._save_state()

    restarted = _executor(monkeypatch, tmp_path)
    position_reads = []
    monkeypatch.setattr(
        restarted,
        "get_positions",
        lambda: position_reads.append(True) or {"AAPL": {"qty": final_qty}},
    )

    restarted._reconcile_terminal_order_positions()

    confirmed = restarted.state["order_submission_confirmed"]
    assert confirmed[first["client_order_id"]]["position_reconciled"] is True
    assert confirmed[second["client_order_id"]]["position_reconciled"] is True
    assert len(position_reads) == 1

    final_restart = _executor(monkeypatch, tmp_path)
    monkeypatch.setattr(
        final_restart,
        "get_positions",
        lambda: pytest.fail("Reconciled terminal orders must not be processed again"),
    )
    final_restart._reconcile_terminal_order_positions()


def test_terminal_orders_with_same_instant_in_different_timezones_fail_closed(
    monkeypatch, tmp_path
):
    executor = _executor(monkeypatch, tmp_path)
    executor.state["order_submission_confirmed"] = {
        "client-buy": _order(
            side="buy",
            baseline=0.0,
            filled=2.0,
            timestamp="2026-11-01T10:00:00+00:00",
            client_id="buy",
        ),
        "client-sell": _order(
            side="sell",
            baseline=2.0,
            filled=1.0,
            timestamp="2026-11-01T11:00:00+01:00",
            client_id="sell",
        ),
    }
    executor._save_state()
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {"AAPL": {"qty": 1.0}},
    )

    with pytest.raises(RuntimeError, match="ambiguous timestamps"):
        executor._reconcile_terminal_order_positions()

    confirmed = executor.state["order_submission_confirmed"]
    assert confirmed["client-buy"]["position_reconciled"] is False
    assert confirmed["client-sell"]["position_reconciled"] is False
