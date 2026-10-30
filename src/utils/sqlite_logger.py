from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

DB_PATH = Path("logs/stmaa.db")


def _read_process_start_ticks(pid: int) -> Optional[str]:
    """Read Linux /proc process start ticks; None means identity is unverifiable."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None

    # comm (field 2) can contain spaces and parentheses; split after its final ')'.
    closing_paren = stat.rfind(")")
    if closing_paren < 0:
        return None
    fields = stat[closing_paren + 1 :].strip().split()
    # fields[0] is field 3 (state), fields[19] is field 22 (starttime).
    if len(fields) <= 19 or fields[0] in {"Z", "X"}:
        return None
    return fields[19]


def current_process_identity() -> tuple[int, Optional[str]]:
    """Return PID and kernel start-time token, preventing PID-reuse confusion."""
    pid = os.getpid()
    return pid, _read_process_start_ticks(pid)


def process_identity_is_alive(pid: int, start_ticks: str) -> Optional[bool]:
    """True/False when /proc proves identity alive/dead; None when unverifiable."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or not isinstance(start_ticks, str) or not start_ticks:
        return None
    # Without procfs, a missing PID path is not evidence that the process died.
    if not Path("/proc").is_dir():
        return None

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        return None

    closing_paren = stat.rfind(")")
    if closing_paren < 0:
        return None
    fields = stat[closing_paren + 1 :].strip().split()
    if len(fields) <= 19:
        return None
    if fields[0] in {"Z", "X"}:
        return False
    return fields[19] == start_ticks


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
                meta TEXT NOT NULL,
                owner_pid INTEGER,
                owner_start_ticks TEXT
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
        # Migrate databases created before process ownership was recorded.
        run_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "owner_pid" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN owner_pid INTEGER")
        if "owner_start_ticks" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN owner_start_ticks TEXT")

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
    recover_orphaned_runs(db_path=db_path)
    run_id = str(uuid4())
    owner_pid, owner_start_ticks = current_process_identity()
    with db_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, started_at, ended_at, mode, branch, commit_sha, status, meta,
                owner_pid, owner_start_ticks
            )
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, now_utc(), mode, branch, commit_sha, "running", json_dump(meta),
                owner_pid, owner_start_ticks,
            ),
        )
    return run_id


def recover_orphaned_runs(db_path: Path = DB_PATH) -> list[str]:
    """Fail only runs whose recorded PID/start-time pair is provably dead.

    Unknown or legacy ownership is deliberately left untouched: absence of proof
    must not cause a live concurrent process to be marked as orphaned.
    """
    init_db(db_path)
    recovered: list[str] = []
    with db_conn(db_path) as conn:
        # Serialize recovery against other starters/recovery attempts.
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT run_id, owner_pid, owner_start_ticks
            FROM runs
            WHERE status = 'running' AND ended_at IS NULL
            """
        ).fetchall()
        for row in rows:
            if row["owner_pid"] is None or row["owner_start_ticks"] is None:
                continue
            alive = process_identity_is_alive(
                row["owner_pid"], row["owner_start_ticks"]
            )
            if alive is not False:
                continue
            cursor = conn.execute(
                """
                UPDATE runs
                SET ended_at = ?, status = 'failed'
                WHERE run_id = ? AND status = 'running' AND ended_at IS NULL
                """,
                (now_utc(), row["run_id"]),
            )
            if cursor.rowcount == 1:
                recovered.append(row["run_id"])
    return recovered


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



def _require_active_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Reject writes after a run is terminal while preserving FK errors for unknown IDs."""
    row = conn.execute(
        "SELECT status, ended_at FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is not None and (row["status"] != "running" or row["ended_at"] is not None):
        raise ValueError(f"Run {run_id!r} is not active; refusing to append audit data")


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
        # Serialize with end_run so a terminal run cannot receive late audit rows.
        conn.execute("BEGIN IMMEDIATE")
        _require_active_run(conn, run_id)
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
        # Serialize with end_run so a terminal run cannot receive late snapshots.
        conn.execute("BEGIN IMMEDIATE")
        _require_active_run(conn, run_id)
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
