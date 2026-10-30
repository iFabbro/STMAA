import json
import re

import hub


def _patch_render(monkeypatch, state_file):
    monkeypatch.setattr(hub, "HUB_STATE", state_file)
    monkeypatch.setattr(hub, "clear_screen", lambda: None)
    monkeypatch.setattr(
        hub,
        "get_live_quotes",
        lambda tickers: {
            ticker: {"price": 100.0, "change_pct": 1.23} for ticker in tickers
        },
    )
    monkeypatch.setattr(hub, "write_snapshot", lambda *args, **kwargs: None)


def test_load_state_missing_or_invalid(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(hub, "HUB_STATE", missing)
    assert hub.load_state() == {}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-valid-json")
    monkeypatch.setattr(hub, "HUB_STATE", invalid)
    assert hub.load_state() == {}

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[]")
    monkeypatch.setattr(hub, "HUB_STATE", not_an_object)
    assert hub.load_state() == {}


def test_render_uses_snapshot_state(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "hub_state.json"
    state_file.write_text(json.dumps({
        "timestamp": "2026-08-01T12:00:00",
        "account_data_fresh": True,
        "account_data_source": "alpaca_account",
        "account_snapshot_at": "2026-08-01T11:59:00",
        "mode": "paper",
        "portfolio_value": 123456.0,
        "cash": 1000.0,
        "equity": 123400.0,
        "equity_curve": [120000.0, 121000.0, 123400.0],
        "orders": [
            {
                "ticker": "AAPL",
                "side": "buy",
                "quantity": 1.25,
                "price": 100.0,
                "timestamp": "2026-08-01T11:59:00"
            }
        ],
        "metrics": {
            "max_drawdown": -0.05,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "calmar_ratio": 0.9,
            "exposure": 0.6,
            "regime": "bull",
            "total_return": 0.12,
            "annualized_return": 0.08
        }
    }))

    _patch_render(monkeypatch, state_file)
    hub.render()
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert "STMAA TRADING HUB" in out
    assert "Portfolio Value" in out
    assert "$123,456.00" in out
    assert "AAPL" in out
    assert "BULL" in out
    assert "Account snapshot fresh" in out
    assert "paper" in out
    assert "Orders in last cycle: 1" in out


def test_render_missing_state_does_not_invent_financial_values_or_status(
    tmp_path, monkeypatch, capsys
):
    state_file = tmp_path / "missing_state.json"
    _patch_render(monkeypatch, state_file)
    monkeypatch.setattr(
        hub, "get_live_quotes",
        lambda tickers: {ticker: {"price": 0.0, "change_pct": 0.0} for ticker in tickers},
    )

    hub.render()
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert "n/a (no persisted timestamp)" in out
    assert "unknown (not persisted)" in out
    assert "Account snapshot unavailable or stale" in out
    assert "n/a (not persisted)" in out
    assert "no valid persisted equity history" in out
    assert "Quote feed: unavailable" in out
    assert "System: " not in out or "Active" not in out
    assert "$100,000.00" not in out
    assert "$50,000.00" not in out
    assert "$500,000.00" not in out
    assert "Paper Trading" not in out


def test_render_empty_or_invalid_equity_curve_is_safe(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "hub_state.json"
    state_file.write_text(json.dumps({
        "timestamp": "2026-08-01T12:00:00",
        "portfolio_value": 100000.0,
        "cash": 50000.0,
        "equity": 99500.0,
        "equity_curve": [100000.0, "invalid"],
        "orders": [],
        "account_data_fresh": False,
    }))
    _patch_render(monkeypatch, state_file)

    hub.render()
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert "no valid persisted equity history" in out
    assert "n/a (insufficient persisted history)" in out
    assert "Account snapshot unavailable or stale" in out



def test_finite_number_rejects_booleans():
    assert hub._finite_number(True) is None
    assert hub._finite_number(False) is None


def test_render_rejects_boolean_financial_and_metric_values(
    tmp_path, monkeypatch, capsys
):
    state_file = tmp_path / "boolean_state.json"
    state_file.write_text(json.dumps({
        "timestamp": "2026-10-30T12:00:00",
        "account_data_fresh": True,
        "mode": "paper",
        "portfolio_value": True,
        "cash": False,
        "equity": True,
        "equity_curve": [100000.0, 101000.0],
        "orders": [],
        "metrics": {
            "max_drawdown": True,
            "sharpe_ratio": False,
            "sortino_ratio": True,
            "calmar_ratio": False,
            "exposure": True,
            "total_return": False,
            "annualized_return": True,
        },
    }))
    _patch_render(monkeypatch, state_file)

    hub.render()
    out = re.sub(r"\\x1b\\[[0-9;]*m", "", capsys.readouterr().out)

    assert "$1.00" not in out
    assert "$0.00" not in out
    assert "n/a (not persisted)" in out
    assert "Max Drawdown" in out
    assert "Sharpe Ratio" in out
