from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.dashboard.local_providers import (
    SourceStatus,
    _open_readonly_database,
    read_json_snapshot,
    read_latest_run,
    read_latest_snapshot,
    read_recent_events,
)


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
                mode TEXT NOT NULL, branch TEXT, commit_sha TEXT, status TEXT NOT NULL,
                meta TEXT NOT NULL, owner_pid INTEGER, owner_start_ticks TEXT
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                run_id TEXT NOT NULL, source TEXT NOT NULL, agent_id TEXT,
                event_type TEXT NOT NULL, level TEXT NOT NULL, symbol TEXT,
                message TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                run_id TEXT NOT NULL, source TEXT NOT NULL, portfolio_value REAL,
                cash REAL, equity REAL, drawdown REAL, exposure REAL, regime TEXT,
                positions_json TEXT, payload TEXT NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_json_provider_distinguishes_missing_invalid_and_valid(tmp_path):
    missing = read_json_snapshot(tmp_path / "missing.json")
    assert missing.status is SourceStatus.MISSING

    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text("{broken", encoding="utf-8")
    assert read_json_snapshot(malformed_path).status is SourceStatus.INVALID

    non_object_path = tmp_path / "array.json"
    non_object_path.write_text("[]", encoding="utf-8")
    assert read_json_snapshot(non_object_path).status is SourceStatus.INVALID

    invalid_number_path = tmp_path / "invalid-number.json"
    invalid_number_path.write_text(
        json.dumps({"timestamp": "2026-11-07T10:00:00+00:00", "equity": True}),
        encoding="utf-8",
    )
    assert read_json_snapshot(invalid_number_path).status is SourceStatus.INVALID

    valid_path = tmp_path / "valid.json"
    valid_path.write_text(
        json.dumps({
            "timestamp": "2026-11-07T10:00:00+00:00",
            "equity": 1250.5,
            "unknown_private_field": "not exposed",
        }),
        encoding="utf-8",
    )
    result = read_json_snapshot(valid_path)
    assert result.status is SourceStatus.OK
    assert result.value == {
        "timestamp": "2026-11-07T10:00:00+00:00",
        "equity": 1250.5,
    }

    nullable_path = tmp_path / "nullable.json"
    nullable_path.write_text(
        json.dumps({"ts": "2026-11-07T10:00:00+00:00", "cash": None, "equity_curve": []}),
        encoding="utf-8",
    )
    nullable = read_json_snapshot(nullable_path)
    assert nullable.status is SourceStatus.OK
    assert nullable.value["cash"] is None
    assert nullable.value["equity_curve"] == []

    invalid_curve_path = tmp_path / "invalid-curve.json"
    invalid_curve_path.write_text(
        json.dumps({"timestamp": "2026-11-07T10:00:00+00:00", "equity_curve": [100.0, True]}),
        encoding="utf-8",
    )
    assert read_json_snapshot(invalid_curve_path).status is SourceStatus.INVALID


def test_json_provider_reports_stale_and_missing_timestamp(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"timestamp": "2026-11-07T09:00:00+00:00", "cash": 100.0}),
        encoding="utf-8",
    )
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert read_json_snapshot(path, max_age_seconds=60, now=now).status is SourceStatus.STALE

    path.write_text(json.dumps({"equity": 100.0}), encoding="utf-8")
    assert read_json_snapshot(path).status is SourceStatus.MISSING
    missing_time = read_json_snapshot(path, max_age_seconds=60, now=now)
    assert missing_time.status is SourceStatus.MISSING
    assert missing_time.value == {"equity": 100.0}


def test_json_provider_does_not_modify_source_bytes(tmp_path):
    path = tmp_path / "snapshot.json"
    original = b'{"timestamp":"2026-11-07T10:00:00+00:00","equity":100.0}\n'
    path.write_bytes(original)

    read_json_snapshot(path)

    assert path.read_bytes() == original


def test_sqlite_provider_does_not_create_missing_database(tmp_path):
    path = tmp_path / "absent" / "stmaa.db"
    result = read_latest_snapshot(path)
    assert result.status is SourceStatus.MISSING
    assert not path.exists()
    assert not path.parent.exists()


def test_sqlite_connection_is_readonly_and_query_only(tmp_path):
    path = tmp_path / "stmaa.db"
    _create_database(path)
    before = path.read_bytes()

    with _open_readonly_database(path) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (id INTEGER)")

    assert path.read_bytes() == before


def test_sqlite_providers_return_empty_then_read_bounded_rows(tmp_path):
    path = tmp_path / "stmaa.db"
    _create_database(path)

    assert read_latest_snapshot(path).status is SourceStatus.EMPTY
    assert read_latest_run(path).status is SourceStatus.EMPTY
    assert read_recent_events(path).status is SourceStatus.EMPTY

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO runs (run_id, started_at, mode, status, meta) VALUES (?, ?, ?, ?, ?)",
            ("run-1", "2026-11-07T10:00:00+00:00", "hub", "completed", "{}"),
        )
        connection.execute(
            "INSERT INTO snapshots (ts, run_id, source, equity, payload) VALUES (?, ?, ?, ?, ?)",
            ("2026-11-07T10:00:00+00:00", "run-1", "test-fixture", 1000.0, "{}"),
        )
        for index in range(3):
            connection.execute(
                "INSERT INTO events (ts, run_id, source, event_type, level, message, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("2026-11-07T10:00:00+00:00", "run-1", "test-fixture", "test", "INFO", str(index), "{}"),
            )
        connection.commit()
    finally:
        connection.close()

    before_reads = path.read_bytes()
    snapshot = read_latest_snapshot(path)
    run = read_latest_run(path)
    events = read_recent_events(path, limit=2)
    assert path.read_bytes() == before_reads
    assert snapshot.status is SourceStatus.OK
    assert snapshot.value["equity"] == 1000.0
    assert run.status is SourceStatus.OK
    assert run.value["status"] == "completed"
    assert events.status is SourceStatus.OK
    assert len(events.value) == 2
    assert [event["message"] for event in events.value] == ["2", "1"]


def test_sqlite_provider_reports_invalid_schema_and_invalid_values(tmp_path):
    path = tmp_path / "incomplete.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    assert read_latest_snapshot(path).status is SourceStatus.INVALID

    malformed = tmp_path / "malformed-value.db"
    _create_database(malformed)
    connection = sqlite3.connect(malformed)
    connection.execute(
        "INSERT INTO snapshots (ts, run_id, source, equity, payload) VALUES (?, ?, ?, ?, ?)",
        ("2026-11-07T10:00:00+00:00", "run-1", "test-fixture", float("inf"), "{}"),
    )
    connection.commit()
    connection.close()
    assert read_latest_snapshot(malformed).status is SourceStatus.INVALID


def test_recent_events_limit_is_validated():
    with pytest.raises(ValueError):
        read_recent_events(Path("unused.db"), limit=0)
    with pytest.raises(ValueError):
        read_recent_events(Path("unused.db"), limit=101)


def test_provider_module_has_no_broker_or_trading_dependencies():
    source = Path("src/dashboard/local_providers.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "alpaca",
        "brokerexecutor",
        "src.execution",
        "stable_baselines",
        "ppo",
        "submit_order",
        "cancel_order",
        "close_position",
        "requests.",
        "httpx",
    )
    assert all(token not in source for token in forbidden)



def test_dashboard_audit_writes_are_confined_to_hub_lifecycle():
    hub_source = Path("hub.py").read_text(encoding="utf-8")
    provider_source = Path("src/dashboard/local_providers.py").read_text(encoding="utf-8")

    assert "start_run(" in hub_source
    assert "log_event(" in hub_source
    assert "end_run(" in hub_source
    for writer in ("start_run(", "log_event(", "write_snapshot(", "end_run(", "init_db(", "recover_orphaned_runs("):
        assert writer not in provider_source


def test_json_provider_validates_persisted_position_contract(tmp_path):
    path = tmp_path / "positions.json"
    valid = {
        "timestamp": "2026-11-08T09:55:00+00:00",
        "positions_status": "OK",
        "positions_source": "alpaca_positions",
        "positions_snapshot_at": "2026-11-08T09:55:00+00:00",
        "positions": [{
            "symbol": "AAPL", "qty": 2.0, "market_value": 400.0,
            "avg_entry_price": 190.0, "unrealized_pl": 20.0,
        }],
    }
    path.write_text(json.dumps(valid), encoding="utf-8")
    result = read_json_snapshot(path)
    assert result.status is SourceStatus.OK
    assert result.value["positions"][0]["symbol"] == "AAPL"

    invalid = dict(valid)
    invalid["positions"] = [{"symbol": "AAPL", "qty": True, "market_value": 400.0, "avg_entry_price": 190.0, "unrealized_pl": 20.0}]
    path.write_text(json.dumps(invalid), encoding="utf-8")
    assert read_json_snapshot(path).status is SourceStatus.INVALID

    duplicate = dict(valid)
    duplicate["positions"] = valid["positions"] * 2
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    assert read_json_snapshot(path).status is SourceStatus.INVALID


def test_json_provider_accepts_unavailable_positions_without_fabricating_empty_list(tmp_path):
    path = tmp_path / "positions-unavailable.json"
    path.write_text(json.dumps({
        "timestamp": "2026-11-08T09:55:00+00:00",
        "positions": None,
        "positions_status": "UNAVAILABLE",
        "positions_source": "alpaca_positions",
        "positions_snapshot_at": None,
    }), encoding="utf-8")
    result = read_json_snapshot(path)
    assert result.status is SourceStatus.OK
    assert result.value["positions"] is None

def test_json_provider_rejects_materially_future_timestamps(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps({"timestamp": "2026-11-07T10:02:00+00:00", "equity": 100.0}),
        encoding="utf-8",
    )
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    result = read_json_snapshot(path, max_age_seconds=60, now=now)
    assert result.status is SourceStatus.INVALID

def test_json_provider_rejects_future_timestamps_without_freshness_threshold(tmp_path):
    path = tmp_path / "future-no-threshold.json"
    path.write_text(
        json.dumps({"timestamp": "2026-11-07T10:02:00+00:00", "equity": 100.0}),
        encoding="utf-8",
    )
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    assert read_json_snapshot(path, now=now).status is SourceStatus.INVALID
