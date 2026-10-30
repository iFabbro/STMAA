import sqlite3

import pytest

from src.utils.sqlite_logger import db_conn, log_event, start_run, write_snapshot


def test_db_connection_enables_sqlite_foreign_key_enforcement(tmp_path):
    database = tmp_path / "stmaa.db"

    with db_conn(database) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_events_and_snapshots_reject_unknown_run_ids(tmp_path):
    database = tmp_path / "stmaa.db"
    start_run(mode="test", db_path=database)

    with pytest.raises(sqlite3.IntegrityError):
        log_event(
            "missing-run",
            "test",
            "invalid",
            "must not be persisted",
            db_path=database,
        )

    with pytest.raises(sqlite3.IntegrityError):
        write_snapshot(
            "missing-run",
            "test",
            equity=1000.0,
            db_path=database,
        )


def test_events_and_snapshots_accept_existing_run_ids(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)

    log_event(
        run_id,
        "test",
        "startup",
        "valid event",
        db_path=database,
    )
    write_snapshot(
        run_id,
        "test",
        equity=1000.0,
        db_path=database,
    )

    with db_conn(database) as conn:
        event_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        snapshot_count = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

    assert event_count == 1
    assert snapshot_count == 1
