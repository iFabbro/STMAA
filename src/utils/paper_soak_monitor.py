"""Read-only heartbeat and event recorder for a controlled Alpaca Paper soak.

This component is deliberately broker-agnostic: it performs no network requests,
reads no credentials, and cannot submit or mutate broker orders. Broker polling,
stop thresholds, restart recovery, and final soak assessment are separate stages.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Callable

from src.utils.sqlite_logger import DB_PATH, end_run, log_event, start_run

DEFAULT_DURATION_SECONDS = 48 * 60 * 60
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60
MONITOR_SCHEMA_VERSION = 1


def _positive_finite_seconds(value: float, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return result


class PaperSoakMonitor:
    """Record monitor lifecycle and periodic heartbeats in the existing SQLite log."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        duration_seconds: float = DEFAULT_DURATION_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db_path = Path(db_path)
        self.duration_seconds = _positive_finite_seconds(
            duration_seconds, "duration_seconds"
        )
        self.heartbeat_interval_seconds = _positive_finite_seconds(
            heartbeat_interval_seconds, "heartbeat_interval_seconds"
        )
        self._clock = clock
        self.run_id: str | None = None
        self._started_at: float | None = None
        self._heartbeat_sequence = 0

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            raise RuntimeError("monitor session has not started")
        return max(0.0, self._clock() - self._started_at)

    def start(self) -> str:
        if self.run_id is not None:
            raise RuntimeError("monitor session has already started")
        self._started_at = self._clock()
        self.run_id = start_run(
            mode="paper_soak_read_only",
            meta={
                "component": "paper_soak_monitor",
                "monitor_schema_version": MONITOR_SCHEMA_VERSION,
                "duration_seconds": self.duration_seconds,
                "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
                "broker_access": "disabled",
                "order_submission": "disabled",
            },
            db_path=self.db_path,
        )
        self.record_event(
            "session_started",
            "Read-only Paper soak monitor session started",
            payload={
                "duration_seconds": self.duration_seconds,
                "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
                "broker_access": "disabled",
                "order_submission": "disabled",
            },
        )
        return self.run_id

    def record_event(
        self,
        event_type: str,
        message: str,
        level: str = "INFO",
        payload: dict | None = None,
    ) -> None:
        if self.run_id is None:
            raise RuntimeError("monitor session has not started")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        if not isinstance(payload, (dict, type(None))):
            raise TypeError("payload must be a dictionary or None")
        log_event(
            self.run_id,
            source="paper_soak_monitor",
            event_type=event_type.strip(),
            message=message.strip(),
            level=level,
            payload=payload,
            db_path=self.db_path,
        )

    def heartbeat(self) -> int:
        if self.run_id is None:
            raise RuntimeError("monitor session has not started")
        next_sequence = self._heartbeat_sequence + 1
        self.record_event(
            "heartbeat",
            "Paper soak monitor heartbeat",
            payload={
                "sequence": next_sequence,
                "elapsed_seconds": round(self.elapsed_seconds, 3),
                "duration_seconds": self.duration_seconds,
            },
        )
        self._heartbeat_sequence = next_sequence
        return next_sequence

    def finish(self, status: str = "completed", reason: str = "duration_elapsed") -> None:
        if self.run_id is None:
            raise RuntimeError("monitor session has not started")
        if status not in {"completed", "failed"}:
            raise ValueError("status must be 'completed' or 'failed'")
        self.record_event(
            "session_finished" if status == "completed" else "session_failed",
            "Paper soak monitor session finished",
            level="INFO" if status == "completed" else "ERROR",
            payload={
                "status": status,
                "reason": reason,
                "elapsed_seconds": round(self.elapsed_seconds, 3),
                "heartbeat_count": self._heartbeat_sequence,
            },
        )
        end_run(self.run_id, status=status, db_path=self.db_path)

    def run(self, sleep: Callable[[float], None] = time.sleep) -> str:
        """Run a timed heartbeat loop; no broker interaction is performed."""
        run_id = self.start()
        try:
            self.heartbeat()
            while self.elapsed_seconds < self.duration_seconds:
                remaining = self.duration_seconds - self.elapsed_seconds
                sleep(min(self.heartbeat_interval_seconds, remaining))
                if self.elapsed_seconds < self.duration_seconds:
                    self.heartbeat()
            self.finish(status="completed")
        except KeyboardInterrupt:
            self.finish(status="failed", reason="interrupted")
            raise
        except Exception as exc:
            try:
                self.record_event(
                    "monitor_error",
                    "Paper soak monitor encountered an error",
                    level="ERROR",
                    payload={"error_type": type(exc).__name__},
                )
                self.finish(status="failed", reason="monitor_error")
            except Exception:
                # Preserve the original failure; a still-running DB record is
                # intentionally visible for later operational investigation.
                pass
            raise
        return run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the broker-independent STMAA Paper soak heartbeat monitor."
    )
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=48.0,
        help="monitor duration in hours (default: 48)",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        help="heartbeat interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="SQLite event database path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        duration_seconds = _positive_finite_seconds(
            args.duration_hours, "duration_hours"
        ) * 60 * 60
        monitor = PaperSoakMonitor(
            db_path=args.db_path,
            duration_seconds=duration_seconds,
            heartbeat_interval_seconds=args.heartbeat_seconds,
        )
        run_id = monitor.run()
    except KeyboardInterrupt:
        print("MONITOR_STATUS: INTERRUPTED")
        return 130
    except Exception as exc:
        print(f"MONITOR_STATUS: FAILED ({type(exc).__name__})")
        return 1

    print(f"MONITOR_RUN_ID: {run_id}")
    print("MONITOR_STATUS: COMPLETED")
    print(f"EVENT_DATABASE: {args.db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
