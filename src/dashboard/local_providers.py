"""Typed, strictly read-only adapters for approved local dashboard sources.

These providers never initialize, migrate, recover, or write the monitoring
database. Dashboard-session lifecycle audit writes remain in hub.py and are
intentionally outside this module.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Iterator, TypeVar

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
    "positions_status",
    "positions_source",
    "positions_snapshot_at",
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


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


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
    if max_age_seconds is not None and (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(float(max_age_seconds))
        or max_age_seconds < 0
    ):
        raise ValueError("max_age_seconds must be a finite non-negative number")
    timestamp = _parse_timestamp(observed_at)
    if timestamp is None:
        return SourceStatus.MISSING
    if max_age_seconds is None:
        return SourceStatus.OK
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = (reference.astimezone(timezone.utc) - timestamp).total_seconds()
    return SourceStatus.STALE if age > max_age_seconds else SourceStatus.OK


def _validate_snapshot(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    normalized = {key: value for key, value in payload.items() if key in _ALLOWED_SNAPSHOT_FIELDS}
    for key in _NUMERIC_FIELDS.intersection(normalized):
        value = normalized[key]
        if value is not None and not _is_finite_number(value):
            return None, f"invalid numeric field: {key}"
    if "positions_status" in normalized and (
        not isinstance(normalized["positions_status"], str)
        or normalized["positions_status"] not in {"OK", "UNAVAILABLE"}
    ):
        return None, "invalid text field: positions_status"
    if "positions_source" in normalized and normalized["positions_source"] is not None and not isinstance(normalized["positions_source"], str):
        return None, "invalid text field: positions_source"
    if "positions_snapshot_at" in normalized and normalized["positions_snapshot_at"] is not None:
        if _parse_timestamp(normalized["positions_snapshot_at"]) is None:
            return None, "invalid positions snapshot timestamp"
    if normalized.get("positions_status") == "OK" and not normalized.get("positions_snapshot_at"):
        return None, "missing timestamp for verified positions snapshot"
    if "positions" in normalized:
        positions = normalized["positions"]
        if positions is not None and not isinstance(positions, list):
            return None, "invalid collection field: positions"
        if positions is None and normalized.get("positions_status") != "UNAVAILABLE":
            return None, "missing positions collection without unavailable status"
        if isinstance(positions, list):
            seen_symbols = set()
            required = {"symbol", "qty", "market_value", "avg_entry_price", "unrealized_pl"}
            for position in positions:
                if not isinstance(position, dict) or not required.issubset(position):
                    return None, "invalid position record"
                symbol = position.get("symbol")
                if (
                    not isinstance(symbol, str)
                    or not symbol.strip()
                    or symbol != symbol.strip().upper()
                ):
                    return None, "invalid position symbol"
                if symbol in seen_symbols:
                    return None, "duplicate position symbol"
                seen_symbols.add(symbol)
                for field in required - {"symbol"}:
                    if not _is_finite_number(position[field]):
                        return None, f"invalid position numeric field: {field}"
                if position["qty"] == 0 or position["avg_entry_price"] <= 0:
                    return None, "invalid position quantity or average entry price"
    if "equity_curve" in normalized:
        curve = normalized["equity_curve"]
        if not isinstance(curve, list) or any(
            not _is_finite_number(point) for point in curve
        ):
            return None, "invalid numeric collection: equity_curve"
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
    observed_at = normalized.get("timestamp")
    if observed_at is None:
        observed_at = normalized.get("ts")
    if observed_at is not None and _parse_timestamp(observed_at) is None:
        return SourceResult(SourceStatus.INVALID, source, value=normalized, detail="invalid snapshot timestamp")
    status = _freshness_status(observed_at, max_age_seconds, now)
    detail = "snapshot timestamp is missing" if status is SourceStatus.MISSING else None
    return SourceResult(status, source, normalized, observed_at, detail)


@contextmanager
def _open_readonly_database(
    path: Path,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> Iterator[sqlite3.Connection]:
    """Yield an existing SQLite database in URI read-only and query-only modes."""
    resolved = Path(path).expanduser().resolve()
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=timeout_seconds,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


def _sqlite_error(path: Path, error: Exception) -> SourceResult[Any]:
    if not Path(path).expanduser().exists():
        status = SourceStatus.MISSING
        detail = "database file is missing"
    elif isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("no such table", "no such column")
    ):
        status = SourceStatus.INVALID
        detail = "database schema does not match the expected contract"
    elif isinstance(error, sqlite3.DatabaseError) and any(
        marker in str(error).lower() for marker in ("malformed", "not a database")
    ):
        status = SourceStatus.INVALID
        detail = "database file is invalid"
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
        if item is not None and not _is_finite_number(item):
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


def read_persisted_broker_state(path: Path | None = None) -> SourceResult[dict[str, Any]]:
    """Read broker execution evidence from disk without importing or starting the executor.

    This is historical local state, not a broker query. The provider never creates
    the parent directory, initializes a missing state file, migrates schemas, or
    writes reconciliation results.
    """
    import os

    if path is None:
        configured = os.getenv("STMAA_BROKER_STATE_FILE")
        repository_root = Path(__file__).resolve().parents[2]
        path = (
            Path(configured).expanduser()
            if configured
            else repository_root / "logs" / "execution" / "broker_state.json"
        )
        if not path.is_absolute():
            path = repository_root / path
    source = str(path)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SourceResult(SourceStatus.MISSING, source, detail="broker state file is missing")
    except (PermissionError, OSError):
        return SourceResult(SourceStatus.UNAVAILABLE, source, detail="broker state file cannot be read")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SourceResult(SourceStatus.INVALID, source, detail="broker state is not valid UTF-8 JSON")

    if not isinstance(payload, dict):
        return SourceResult(SourceStatus.INVALID, source, detail="broker state root must be an object")

    schema = payload.get("state_schema_version")
    revision = payload.get("state_revision", 0)
    if type(schema) is not int or schema not in {1, 2}:
        return SourceResult(SourceStatus.INVALID, source, detail="unsupported broker state schema")
    if type(revision) is not int or revision < 0:
        return SourceResult(SourceStatus.INVALID, source, detail="invalid broker state revision")

    required_maps = (
        "order_submission_unresolved",
        "order_submission_confirmed",
        "emergency_liquidation_pending",
        "emergency_liquidation_unresolved",
    )
    for key in required_maps:
        value = payload.get(key)
        if not isinstance(value, dict):
            return SourceResult(SourceStatus.INVALID, source, detail=f"invalid broker state map: {key}")

    for key in ("order_submission_unresolved", "order_submission_confirmed",
                "emergency_liquidation_unresolved"):
        if any(not isinstance(item, dict) for item in payload[key].values()):
            return SourceResult(SourceStatus.INVALID, source, detail=f"invalid record in {key}")

    if any(not isinstance(order_id, str) or not order_id.strip()
           for order_id in payload["emergency_liquidation_pending"].values()):
        return SourceResult(SourceStatus.INVALID, source, detail="invalid order ID in emergency liquidation pending map")

    opg_batches = payload.get("opg_batches")
    if schema == 2:
        if not isinstance(opg_batches, dict) or any(
            not isinstance(batch, dict) for batch in opg_batches.values()
        ):
            return SourceResult(SourceStatus.INVALID, source, detail="invalid OPG batch map")
    else:
        # Schema v1 predates persisted OPG batches; do not infer an empty v2 map.
        opg_batches = None

    account_mode = payload.get("account_mode")
    if account_mode is not None and account_mode not in {"paper", "live"}:
        return SourceResult(SourceStatus.INVALID, source, detail="invalid account mode")

    value = {
        "state_schema_version": schema,
        "state_revision": revision,
        "account_mode": account_mode,
        "account_id": payload.get("account_id"),
        "order_submission_unresolved": payload["order_submission_unresolved"],
        "order_submission_confirmed": payload["order_submission_confirmed"],
        "emergency_liquidation_pending": payload["emergency_liquidation_pending"],
        "emergency_liquidation_unresolved": payload["emergency_liquidation_unresolved"],
        "opg_batches": opg_batches,
    }
    return SourceResult(SourceStatus.OK, source, value=value)
