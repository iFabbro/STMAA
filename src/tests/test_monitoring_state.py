import json

import pytest

import hub
import trade
from src.utils import monitoring_state


def test_write_hub_state_atomically_creates_complete_json(tmp_path):
    destination = tmp_path / "logs" / "hub_state.json"
    payload = {
        "timestamp": "2026-10-30T12:00:00",
        "mode": "paper",
        "account_data_fresh": True,
        "equity": 123400.0,
        "metrics": {"max_drawdown": -0.05, "sharpe_ratio": 1.2},
        "orders": [{"ticker": "AAPL", "side": "buy"}],
    }

    monitoring_state.write_hub_state(payload, destination)

    assert json.loads(destination.read_text()) == payload
    assert list(destination.parent.glob(".hub_state.json.*.tmp")) == []


def test_write_hub_state_preserves_previous_snapshot_when_replace_fails(
    tmp_path, monkeypatch
):
    destination = tmp_path / "hub_state.json"
    previous = {"timestamp": "old", "metrics": {"sharpe_ratio": 0.8}}
    destination.write_text(json.dumps(previous))

    def fail_replace(source, target):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(monitoring_state.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        monitoring_state.write_hub_state({"timestamp": "new"}, destination)

    assert json.loads(destination.read_text()) == previous
    assert list(tmp_path.glob(".hub_state.json.*.tmp")) == []


def test_trade_update_hub_state_persists_dashboard_fields_and_metrics(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    class AccountExecutor:
        paper_trading = True

        def get_account_status(self):
            return {
                "portfolio_value": 123456.0,
                "cash": 1000.0,
                "equity": 123400.0,
                "account_data_fresh": True,
                "account_data_source": "alpaca_account",
                "account_snapshot_at": "2026-10-30T11:59:00",
            }

    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.executor = AccountExecutor()
    bot.equity_curve = [122000.0]
    result = {
        "timestamp": "2026-10-30T12:00:00",
        "orders": [{"ticker": "AAPL", "side": "buy", "quantity": 1.0}],
        "pre_execution_portfolio_value": 122000.0,
        "pre_execution_equity": 122000.0,
        "metrics_equity": 123400.0,
        "metrics": {
            "max_drawdown": -0.05,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.4,
            "calmar_ratio": 0.9,
            "exposure": 0.6,
            "regime": "bull",
            "total_return": 0.12,
            "annualized_return": 0.08,
        },
    }

    bot.update_hub_state(result)
    state = hub.load_state()

    assert state["mode"] == "paper"
    assert state["account_data_fresh"] is True
    assert state["account_data_source"] == "alpaca_account"
    assert state["account_snapshot_at"] == "2026-10-30T11:59:00"
    assert state["portfolio_value"] == 123456.0
    assert state["cash"] == 1000.0
    assert state["equity"] == 123400.0
    assert state["equity_curve"] == [122000.0, 123400.0]
    assert state["orders"] == result["orders"]
    assert state["metrics"] == result["metrics"]
    assert state["metrics"]["sharpe_ratio"] == 1.2
    assert state["last_result"] == result


def test_trade_update_hub_state_fails_closed_on_stale_account(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class StaleExecutor:
        paper_trading = True

        def get_account_status(self):
            return {"account_data_fresh": False}

    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.executor = StaleExecutor()
    bot.equity_curve = []

    with pytest.raises(RuntimeError, match="fresh broker account snapshot"):
        bot.update_hub_state({"orders": [], "metrics": {}})

    assert not (tmp_path / "logs" / "hub_state.json").exists()



def test_load_equity_curve_restores_last_120_finite_points(tmp_path):
    destination = tmp_path / "hub_state.json"
    points = [float(value) for value in range(1, 126)]
    destination.write_text(json.dumps({"equity_curve": points}), encoding="utf-8")

    restored = monitoring_state.load_equity_curve(destination)

    assert restored == points[-120:]
    assert len(restored) == 120


@pytest.mark.parametrize(
    "payload",
    [
        "{malformed",
        json.dumps([]),
        json.dumps({"equity_curve": "not-a-list"}),
        json.dumps({"equity_curve": [100.0, "invalid"]}),
        json.dumps({"equity_curve": [100.0, float("nan")]}),
        json.dumps({"equity_curve": [100.0, True]}),
    ],
)
def test_load_equity_curve_returns_empty_for_invalid_snapshot(tmp_path, payload):
    destination = tmp_path / "hub_state.json"
    destination.write_text(payload, encoding="utf-8")

    assert monitoring_state.load_equity_curve(destination) == []


def test_load_equity_curve_returns_empty_when_snapshot_is_missing(tmp_path):
    assert monitoring_state.load_equity_curve(tmp_path / "missing.json") == []


def test_load_equity_curve_handles_invalid_utf8_snapshot(tmp_path):
    destination = tmp_path / "hub_state.json"
    destination.write_bytes(bytes([0xFF, 0xFE, 0xFA]))

    assert monitoring_state.load_equity_curve(destination) == []


def test_dashboard_equity_curve_rejects_boolean_observations():
    assert hub._valid_curve([100.0, True]) == []
    assert hub._valid_curve([100.0, False]) == []


def test_dashboard_load_state_handles_invalid_utf8_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    destination = tmp_path / "logs" / "hub_state.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(bytes([0xFF, 0xFE, 0xFA]))

    assert hub.load_state() == {}
