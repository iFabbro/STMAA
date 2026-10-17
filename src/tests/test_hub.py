import json

import hub


def test_load_state_missing_or_invalid(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(hub, "HUB_STATE", missing)
    assert hub.load_state() == {}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-valid-json")
    monkeypatch.setattr(hub, "HUB_STATE", invalid)
    assert hub.load_state() == {}


def test_render_uses_snapshot_state(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "hub_state.json"
    state_file.write_text(json.dumps({
        "timestamp": "2026-08-01T12:00:00",
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

    monkeypatch.setattr(hub, "HUB_STATE", state_file)
    monkeypatch.setattr(hub, "clear_screen", lambda: None)
    monkeypatch.setattr(hub, "get_live_quotes", lambda tickers: {
        t: {"price": 100.0, "change_pct": 1.23} for t in tickers
    })
    monkeypatch.setattr(hub, "write_snapshot", lambda *args, **kwargs: None)

    hub.render()
    out = capsys.readouterr().out

    assert "STMAA TRADING HUB" in out
    assert "Portfolio Value" in out
    assert "$123,456.00" in out
    assert "AAPL" in out
    assert "BULL" in out
    assert "Orders in last cycle:" in out
