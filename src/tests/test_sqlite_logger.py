import sqlite3
from pathlib import Path

import pytest

from src.utils import sqlite_logger
from src.utils.sqlite_logger import db_conn, log_event, start_run, write_snapshot


def test_default_sqlite_database_path_is_anchored_to_repository():
    expected = Path(sqlite_logger.__file__).resolve().parents[2] / "logs" / "stmaa.db"

    assert sqlite_logger.DB_PATH.is_absolute()
    assert sqlite_logger.DB_PATH == expected


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


def test_events_and_snapshots_reject_terminal_runs(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)
    from src.utils.sqlite_logger import end_run

    end_run(run_id, status="completed", db_path=database)

    with pytest.raises(ValueError, match="not active"):
        log_event(
            run_id,
            "test",
            "late-event",
            "must not be persisted",
            db_path=database,
        )

    with pytest.raises(ValueError, match="not active"):
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

    assert event_count == 0
    assert snapshot_count == 0



def test_db_connection_rolls_back_on_keyboard_interrupt(tmp_path):
    database = tmp_path / "stmaa.db"
    with db_conn(database) as conn:
        conn.execute("CREATE TABLE records (value TEXT NOT NULL)")

    with pytest.raises(KeyboardInterrupt):
        with db_conn(database) as conn:
            conn.execute("INSERT INTO records (value) VALUES ('partial')")
            raise KeyboardInterrupt

    with db_conn(database) as conn:
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    assert count == 0


def test_db_connection_closes_when_foreign_key_setup_fails(tmp_path, monkeypatch):
    class FailingConnection:
        closed = False
        rolled_back = False
        row_factory = None

        def execute(self, _statement):
            raise sqlite3.OperationalError("simulated database I/O failure")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = FailingConnection()
    monkeypatch.setattr(
        sqlite_logger.sqlite3, "connect", lambda _path: connection
    )

    with pytest.raises(
        sqlite3.OperationalError, match="simulated database I/O failure"
    ):
        with db_conn(tmp_path / "stmaa.db"):
            pass

    assert connection.rolled_back
    assert connection.closed
