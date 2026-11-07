"""Typed, strictly read-only adapters for approved local dashboard sources.

These providers never initialize, migrate, recover, or write the monitoring
database. Dashboard-session lifecycle audit writes remain in hub.py and are
intentionally outside this module.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar

from src.utils.monitoring_state import HUB_STATE_PATH
from src.utils.sqlite_logger import DB_PATH

T = TypeVar("T")
MAX_EVENT_ROWS = 100
DEFAULT_QUERY_TIMEOUT_SECONDS = 1.0
_NUMERIC_FIELDS = {
    "portfolio_value",
    "equity",
    "cash",
    "drawdown",
    "exposure",
}
_ALLOWED_SNAPSHOT_FIELDS = {
    "timestamp",
    "ts",
    "source",
    "run_id",
    "portfolio_value",
    "equity",
    "cash",
    "drawdown",
    "exposure",
    "regime",
    "positions",
    "positions_json",
    "equity_curve",
    "mode",
}


class SourceStatus(str, Enum):
    """Stable source state; EMPTY is distinct from missing or invalid."""

    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class SourceResult(Generic[T]):
    """Value plus provenance and status; no implicit fallback values."""

    status: SourceStatus
    source: str
    value: T | None = None
    observed_at: str | None = None
    detail: str | None = None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _freshness_status(
    observed_at: str | None,
    max_age_seconds: float | None,
    now: datetime | None = None,
) -> SourceStatus:
    if max_age_seconds is None:
        return SourceStatus.OK
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(float(max_age_seconds))
        or max_age_seconds < 0
    ):
        raise ValueError("max_age_seconds must be a finite non-negative number")
    timestamp = _parse_timestamp(observed_at)
    if timestamp is None:
        return SourceStatus.MISSING
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = (reference.astimezone(timezone.utc) - timestamp).total_seconds()
    return SourceStatus.STALE if age > max_age_seconds else SourceStatus.OK


def _validate_snapshot(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    normalized = {key: value for key, value in payload.items() if key in _ALLOWED_SNAPSHOT_FIELDS}
    for key in _NUMERIC_FIELDS.intersection(normalized):
        value = normalized[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            return None, f"invalid numeric field: {key}"
    for key in ("positions", "equity_curve"):
        if key in normalized and not isinstance(normalized[key], list):
            return None, f"invalid collection field: {key}"
    if "positions_json" in normalized and not isinstance(
        normalized["positions_json"], (str, dict, list, type(None))
    ):
        return None, "invalid collection field: positions_json"
    if "regime" in normalized and normalized["regime"] is not None and not isinstance(
        normalized["regime"], str
    ):
        return None, "invalid text field: regime"
    return normalized, None


def read_json_snapshot(
    path: Path = HUB_STATE_PATH,
    *,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> SourceResult[dict[str, Any]]:
    """Read and validate the allow-listed fields of the existing JSON snapshot."""
    source = str(path)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SourceResult(SourceStatus.MISSING, source, detail="snapshot file is missing")
    except (PermissionError, OSError):
        return SourceResult(SourceStatus.UNAVAILABLE, source, detail="snapshot cannot be read")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SourceResult(SourceStatus.INVALID, source, detail="snapshot is not valid UTF-8 JSON")

    if not isinstance(payload, dict):
        return SourceResult(SourceStatus.INVALID, source, detail="snapshot root must be an object")
    normalized, error = _validate_snapshot(payload)
    if error:
        return SourceResult(SourceStatus.INVALID, source, detail=error)
    observed_at = normalized.get("timestamp", normalized.get("ts"))
    if observed_at is not None and _parse_timestamp(observed_at) is None:
        return SourceResult(SourceStatus.INVALID, source, value=normalized, detail="invalid snapshot timestamp")
    status = _freshness_status(observed_at, max_age_seconds, now)
    detail = "snapshot timestamp is missing" if status is SourceStatus.MISSING else None
    return SourceResult(status, source, normalized, observed_at, detail)


def _open_readonly_database(
    path: Path,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> sqlite3.Connection:
    """Open an existing SQLite database in URI read-only and query-only modes."""
    resolved = Path(path).expanduser().resolve()
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=timeout_seconds,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _sqlite_error(path: Path, error: Exception) -> SourceResult[Any]:
    if isinstance(error, FileNotFoundError):
        status = SourceStatus.MISSING
        detail = "database file is missing"
    elif isinstance(error, sqlite3.OperationalError) and "no such table" in str(error).lower():
        status = SourceStatus.INVALID
        detail = "database schema does not contain the expected table"
    elif isinstance(error, sqlite3.DatabaseError):
        status = SourceStatus.INVALID
        detail = "database is invalid or unreadable"
    else:
        status = SourceStatus.UNAVAILABLE
        detail = "database cannot be read"
    return SourceResult(status, str(path), detail=detail)


def read_latest_snapshot(
    path: Path = DB_PATH,
    *,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> SourceResult[dict[str, Any]]:
    """Read one latest SQLite snapshot row using a bounded SELECT."""
    query = (
        "SELECT id, ts, run_id, source, portfolio_value, cash, equity, "
        "drawdown, exposure, regime, positions_json, payload "
        "FROM snapshots ORDER BY id DESC LIMIT 1"
    )
    try:
        with _open_readonly_database(path) as connection:
            row = connection.execute(query).fetchone()
    except (OSError, sqlite3.Error) as error:
        return _sqlite_error(path, error)
    if row is None:
        return SourceResult(SourceStatus.EMPTY, str(path), detail="snapshots table contains no rows")
    value = dict(row)
    for key in _NUMERIC_FIELDS.intersection(value):
        item = value[key]
        if item is not None and (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            return SourceResult(SourceStatus.INVALID, str(path), detail=f"invalid numeric field: {key}")
    status = _freshness_status(value.get("ts"), max_age_seconds, now)
    detail = "snapshot timestamp is missing" if status is SourceStatus.MISSING else None
    return SourceResult(status, str(path), value, value.get("ts"), detail)


def read_latest_run(path: Path = DB_PATH) -> SourceResult[dict[str, Any]]:
    """Read the latest local run metadata; never infer that it is still alive."""
    query = (
        "SELECT run_id, started_at, ended_at, mode, branch, commit_sha, "
        "status, meta, owner_pid, owner_start_ticks "
        "FROM runs ORDER BY started_at DESC LIMIT 1"
    )
    try:
        with _open_readonly_database(path) as connection:
            row = connection.execute(query).fetchone()
    except (OSError, sqlite3.Error) as error:
        return _sqlite_error(path, error)
    if row is None:
        return SourceResult(SourceStatus.EMPTY, str(path), detail="runs table contains no rows")
    value = dict(row)
    return SourceResult(SourceStatus.OK, str(path), value, value.get("started_at"))


def read_recent_events(
    path: Path = DB_PATH,
    *,
    limit: int = 20,
) -> SourceResult[list[dict[str, Any]]]:
    """Read a bounded page of local events, newest first."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVENT_ROWS:
        raise ValueError(f"limit must be an integer between 1 and {MAX_EVENT_ROWS}")
    query = (
        "SELECT id, ts, run_id, source, agent_id, event_type, level, "
        "symbol, message, payload FROM events ORDER BY id DESC LIMIT ?"
    )
    try:
        with _open_readonly_database(path) as connection:
            rows = connection.execute(query, (limit,)).fetchall()
    except (OSError, sqlite3.Error) as error:
        return _sqlite_error(path, error)
    if not rows:
        return SourceResult(SourceStatus.EMPTY, str(path), value=[], detail="events table contains no rows")
    return SourceResult(SourceStatus.OK, str(path), [dict(row) for row in rows], rows[0]["ts"])
