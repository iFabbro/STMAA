"""Fail-closed stop evaluation for a controlled Paper soak.

This controller consumes health observations supplied by a future monitor
integration. It performs no broker access and never mutates broker orders.
A triggered stop is sticky and is restored from the SQLite event log.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils.sqlite_logger import db_conn

MAX_CONSECUTIVE_API_FAILURES = 3
MAX_HEARTBEAT_AGE_SECONDS = 120.0


@dataclass(frozen=True)
class PaperSoakHealthSnapshot:
    """One complete set of independent health observations.

    None is deliberately permitted at the boundary so that missing telemetry
    can be represented and converted into a fail-closed stop decision.
    """

    broker_endpoint_is_paper: bool | None = None
    order_mutation_attempted: bool | None = None
    broker_state_consistent: bool | None = None
    exposure_determinable: bool | None = None
    market_data_valid: bool | None = None
    market_data_fresh: bool | None = None
    persistence_healthy: bool | None = None
    broker_snapshot_complete: bool | None = None
    api_consecutive_failures: int | None = None
    heartbeat_age_seconds: float | None = None


@dataclass(frozen=True)
class PaperSoakStopDecision:
    stopped: bool
    reason: str | None = None
    details: dict[str, Any] | None = None


class StopPersistenceError(RuntimeError):
    """The stop was latched in memory but could not be durably recorded."""


class PaperSoakStopController:
    """Evaluate soak health and latch the first stop reason permanently."""

    _BOOLEAN_SIGNALS = (
        "broker_endpoint_is_paper",
        "order_mutation_attempted",
        "broker_state_consistent",
        "exposure_determinable",
        "market_data_valid",
        "market_data_fresh",
        "persistence_healthy",
        "broker_snapshot_complete",
    )

    def __init__(self, monitor: PaperSoakMonitor) -> None:
        if monitor.run_id is None:
            raise RuntimeError("monitor session must be started before stop controller")
        self.monitor = monitor
        self._stopped = self._restore_sticky_stop()

    @property
    def stopped(self) -> bool:
        return self._stopped

    def _restore_sticky_stop(self) -> bool:
        with db_conn(self.monitor.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM events
                WHERE run_id = ? AND source = 'paper_soak_stop_controller'
                  AND event_type = 'stop_triggered'
                LIMIT 1
                """,
                (self.monitor.run_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _validate_snapshot(snapshot: PaperSoakHealthSnapshot) -> str | None:
        for field in PaperSoakStopController._BOOLEAN_SIGNALS:
            value = getattr(snapshot, field)
            if type(value) is not bool:
                return f"unknown_or_invalid_signal:{field}"

        failures = snapshot.api_consecutive_failures
        if type(failures) is not int or failures < 0:
            return "unknown_or_invalid_signal:api_consecutive_failures"

        heartbeat_age = snapshot.heartbeat_age_seconds
        if (
            isinstance(heartbeat_age, bool)
            or not isinstance(heartbeat_age, (int, float))
            or not math.isfinite(float(heartbeat_age))
            or heartbeat_age < 0
        ):
            return "unknown_or_invalid_signal:heartbeat_age_seconds"
        return None

    @staticmethod
    def _first_failure(snapshot: PaperSoakHealthSnapshot) -> str | None:
        checks = (
            (not snapshot.broker_endpoint_is_paper, "non_paper_broker_endpoint"),
            (snapshot.order_mutation_attempted, "broker_order_mutation_attempted"),
            (not snapshot.broker_state_consistent, "broker_state_mismatch"),
            (not snapshot.exposure_determinable, "exposure_not_determinable"),
            (not snapshot.market_data_valid, "market_data_invalid"),
            (not snapshot.market_data_fresh, "market_data_stale"),
            (not snapshot.persistence_healthy, "persistence_or_lock_failure"),
            (not snapshot.broker_snapshot_complete, "broker_snapshot_incomplete"),
            (
                snapshot.api_consecutive_failures >= MAX_CONSECUTIVE_API_FAILURES,
                "broker_api_unreachable_threshold",
            ),
            (
                snapshot.heartbeat_age_seconds > MAX_HEARTBEAT_AGE_SECONDS,
                "heartbeat_stale_threshold",
            ),
        )
        return next((reason for failed, reason in checks if failed), None)

    def evaluate(
        self, snapshot: PaperSoakHealthSnapshot
    ) -> PaperSoakStopDecision:
        """Return a stop decision; once stopped, the controller never resumes."""
        if self._stopped:
            return PaperSoakStopDecision(
                stopped=True, reason="stop_already_latched"
            )
        if not isinstance(snapshot, PaperSoakHealthSnapshot):
            reason = "unknown_or_invalid_signal:snapshot"
            details: dict[str, Any] = {"received_type": type(snapshot).__name__}
        else:
            reason = self._validate_snapshot(snapshot)
            details = asdict(snapshot)
            if reason is None:
                reason = self._first_failure(snapshot)

        if reason is None:
            return PaperSoakStopDecision(stopped=False)

        # Latch in memory before attempting I/O: a failed event write must not
        # permit this process to continue as though the session were healthy.
        self._stopped = True
        try:
            self.monitor.record_event(
                "stop_triggered",
                "Paper soak automatic stop threshold triggered",
                level="ERROR",
                payload={
                    "reason": reason,
                    "details": details,
                    "max_consecutive_api_failures": MAX_CONSECUTIVE_API_FAILURES,
                    "max_heartbeat_age_seconds": MAX_HEARTBEAT_AGE_SECONDS,
                },
            )
        except Exception as exc:
            raise StopPersistenceError(
                "stop latched in memory but durable stop event could not be written"
            ) from exc

        return PaperSoakStopDecision(
            stopped=True, reason=reason, details=details
        )
