from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

DB_PATH = Path("logs/stmaa.db")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump(data: Optional[Dict[str, Any]]) -> str:
    return json.dumps(data or {}, ensure_ascii=False, default=str)


@contextmanager
def db_conn(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # SQLite disables foreign-key enforcement by default; enable it per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with db_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                mode TEXT NOT NULL,
                branch TEXT,
                commit_sha TEXT,
                status TEXT NOT NULL,
                meta TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                agent_id TEXT,
                event_type TEXT NOT NULL,
                level TEXT NOT NULL,
                symbol TEXT,
                message TEXT NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                portfolio_value REAL,
                cash REAL,
                equity REAL,
                drawdown REAL,
                exposure REAL,
                regime TEXT,
                positions_json TEXT,
                payload TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run_ts ON events(run_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source_type_ts ON events(source, event_type, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_run_ts ON snapshots(run_id, ts)")


def start_run(
    mode: str = "paper",
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    db_path: Path = DB_PATH,
) -> str:
    init_db(db_path)
    run_id = str(uuid4())
    with db_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, started_at, ended_at, mode, branch, commit_sha, status, meta)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (run_id, now_utc(), mode, branch, commit_sha, "running", json_dump(meta)),
        )
    return run_id


def end_run(run_id: str, status: str = "completed", db_path: Path = DB_PATH) -> None:
    """Finalize an active run exactly once with a terminal status."""
    if status not in {"completed", "failed"}:
        raise ValueError("Run status must be a terminal value: 'completed' or 'failed'")

    init_db(db_path)
    with db_conn(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE runs
            SET ended_at = ?, status = ?
            WHERE run_id = ? AND ended_at IS NULL AND status = 'running'
            """,
            (now_utc(), status, run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(
                f"Run {run_id!r} does not exist or has already been finalized"
            )


def log_event(
    run_id: str,
    source: str,
    event_type: str,
    message: str,
    level: str = "INFO",
    agent_id: Optional[str] = None,
    symbol: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    with db_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events (ts, run_id, source, agent_id, event_type, level, symbol, message, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_utc(), run_id, source, agent_id, event_type, level, symbol, message, json_dump(payload)),
        )


def write_snapshot(
    run_id: str,
    source: str,
    portfolio_value: Optional[float] = None,
    cash: Optional[float] = None,
    equity: Optional[float] = None,
    drawdown: Optional[float] = None,
    exposure: Optional[float] = None,
    regime: Optional[str] = None,
    positions_json: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    with db_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO snapshots (
                ts, run_id, source, portfolio_value, cash, equity, drawdown, exposure, regime, positions_json, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_utc(),
                run_id,
                source,
                portfolio_value,
                cash,
                equity,
                drawdown,
                exposure,
                regime,
                json_dump(positions_json),
                json_dump(payload),
            ),
        )


def latest_snapshot(db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    if not db_path.exists():
        return None
    with db_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def recent_events(limit: int = 20, db_path: Path = DB_PATH) -> list[Dict[str, Any]]:
    if not db_path.exists():
        return []
    with db_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
