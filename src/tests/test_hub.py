import json

import hub


def test_load_state_missing_invalid_and_non_object(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(hub, "HUB_STATE", missing)
    assert hub.load_state() == {}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-valid-json", encoding="utf-8")
    monkeypatch.setattr(hub, "HUB_STATE", invalid)
    assert hub.load_state() == {}

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(hub, "HUB_STATE", not_an_object)
    assert hub.load_state() == {}


def test_load_state_reads_only_dictionary_snapshots(tmp_path, monkeypatch):
    state_file = tmp_path / "hub_state.json"
    expected = {
        "timestamp": "2026-11-07T12:00:00",
        "mode": "paper",
        "equity": 1234.5,
        "orders": [],
    }
    state_file.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(hub, "HUB_STATE", state_file)

    assert hub.load_state() == expected


def test_load_state_handles_invalid_utf8_snapshot(tmp_path, monkeypatch):
    state_file = tmp_path / "hub_state.json"
    state_file.write_bytes(bytes([0xFF, 0xFE, 0xFA]))
    monkeypatch.setattr(hub, "HUB_STATE", state_file)

    assert hub.load_state() == {}


def test_finite_number_rejects_booleans_strings_and_non_finite_values():
    assert hub._finite_number(True) is None
    assert hub._finite_number(False) is None
    assert hub._finite_number("12345.67") is None
    assert hub._finite_number(float("nan")) is None
    assert hub._finite_number(float("inf")) is None
    assert hub._finite_number(12345.67) == 12345.67


def test_valid_curve_rejects_entire_curve_if_any_observation_is_invalid():
    assert hub._valid_curve([100.0, 101.5]) == [100.0, 101.5]
    assert hub._valid_curve([100.0, True]) == []
    assert hub._valid_curve([100.0, "101.0"]) == []
    assert hub._valid_curve([100.0, float("nan")]) == []
    assert hub._valid_curve("not-a-list") == []


def test_render_launches_textual_shell_without_broker_integration(monkeypatch):
    calls = []

    class FakeDashboard:
        def run(self):
            calls.append("run")

    monkeypatch.setattr("src.dashboard.STMAADashboard", FakeDashboard)

    hub.render()

    assert calls == ["run"]


def test_hub_entrypoint_does_not_import_broker_or_trading_clients():
    from pathlib import Path

    source = Path("hub.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "alpaca",
        "brokerexecutor",
        "submit_order",
        "cancel_order",
        "close_position",
        "src.execution",
        "from trade",
        "import trade",
    )
    assert all(token not in source for token in forbidden)
