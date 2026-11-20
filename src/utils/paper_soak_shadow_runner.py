"""Fail-closed shadow observation loop for a planned Paper soak.

The runner accepts an injected observation provider and records its output. It
has no broker client, credentials, network access, or execution dependency.
The provider contract is read-only; this module never calls trading methods.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from src.data.alpaca_feed import TransientMarketDataError
from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils.paper_soak_stop_controller import (
    PaperSoakHealthSnapshot,
    PaperSoakStopController,
)

DEFAULT_OBSERVATION_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_MARKET_DATA_OUTAGE_SECONDS = 10 * 60.0


@dataclass(frozen=True)
class PaperSoakShadowObservation:
    """One read-only health sample and the strategy's hypothetical decision."""

    health: PaperSoakHealthSnapshot
    decision: Mapping[str, Any]


class PaperSoakShadowRunner:
    """Record read-only observations and stop on any unhealthy or unknown input.

    The provider is an integration boundary owned by the caller. It must only
    read data and calculate hypothetical decisions; it must never submit,
    replace, cancel, or close broker orders. The runner deliberately receives
    no executor or broker-client object.
    """

    def __init__(
        self,
        monitor: PaperSoakMonitor,
        observation_provider: Callable[[], PaperSoakShadowObservation],
        observation_interval_seconds: float = DEFAULT_OBSERVATION_INTERVAL_SECONDS,
        max_market_data_outage_seconds: float = DEFAULT_MAX_MARKET_DATA_OUTAGE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(observation_provider):
            raise TypeError("observation_provider must be callable")
        if isinstance(observation_interval_seconds, bool):
            raise ValueError("observation_interval_seconds must be positive and finite")
        try:
            interval = float(observation_interval_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "observation_interval_seconds must be positive and finite"
            ) from exc
        if not (interval > 0 and interval < float("inf")):
            raise ValueError("observation_interval_seconds must be positive and finite")
        try:
            outage_budget = float(max_market_data_outage_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "max_market_data_outage_seconds must be positive and finite"
            ) from exc
        if (
            isinstance(max_market_data_outage_seconds, bool)
            or not (outage_budget > 0 and outage_budget < float("inf"))
        ):
            raise ValueError(
                "max_market_data_outage_seconds must be positive and finite"
            )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.monitor = monitor
        self.observation_provider = observation_provider
        self.observation_interval_seconds = interval
        self.max_market_data_outage_seconds = outage_budget
        self.clock = clock
        self.monitor.run_metadata.setdefault(
            "max_market_data_outage_seconds", self.max_market_data_outage_seconds
        )

    @staticmethod
    def _validate_observation(value: Any) -> PaperSoakShadowObservation:
        if not isinstance(value, PaperSoakShadowObservation):
            raise TypeError(
                "observation provider must return PaperSoakShadowObservation"
            )
        if not isinstance(value.health, PaperSoakHealthSnapshot):
            raise TypeError("observation health must be PaperSoakHealthSnapshot")
        if not isinstance(value.decision, Mapping):
            raise TypeError("shadow decision must be a mapping")
        decision = dict(value.decision)
        # Reject NaN, infinity, and non-JSON objects before writing evidence.
        try:
            json.dumps(decision, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("shadow decision must contain finite JSON values") from exc
        return PaperSoakShadowObservation(
            health=value.health,
            decision=decision,
        )

    def run(self, sleep: Callable[[float], None] = time.sleep) -> str:
        """Run until duration expires or a fail-closed stop is triggered."""
        run_id = self.monitor.start()
        stop_controller = PaperSoakStopController(self.monitor)
        outage_started_at: float | None = None
        outage_id: int | None = None
        next_outage_id = 0

        def stop_for_transport_outage(reason: str, elapsed: float) -> str:
            self.monitor.record_event(
                "stop_triggered",
                "Market-data transport outage exceeded its recovery budget",
                level="ERROR",
                payload={
                    "reason": reason,
                    "outage_id": outage_id,
                    "outage_seconds": round(elapsed, 3),
                    "max_market_data_outage_seconds": self.max_market_data_outage_seconds,
                    "order_submission": "disabled",
                },
            )
            self.monitor.finish(status="failed", reason=reason)
            return run_id

        try:
            while self.monitor.elapsed_seconds < self.monitor.duration_seconds:
                if outage_started_at is not None:
                    outage_elapsed = max(0.0, self.clock() - outage_started_at)
                    if outage_elapsed >= self.max_market_data_outage_seconds:
                        return stop_for_transport_outage(
                            "market_data_outage_budget_exhausted", outage_elapsed
                        )

                request_started_at = self.clock()
                try:
                    raw_observation = self.observation_provider()
                except TransientMarketDataError as exc:
                    now = self.clock()
                    if outage_started_at is None:
                        next_outage_id += 1
                        outage_id = next_outage_id
                        outage_started_at = request_started_at
                        set_deadline = getattr(
                            self.observation_provider,
                            "set_market_data_outage_deadline",
                            None,
                        )
                        if callable(set_deadline):
                            set_deadline(
                                outage_started_at + self.max_market_data_outage_seconds
                            )
                        self.monitor.record_event(
                            "market_data_outage_started",
                            "Market-data transport became unavailable; recovery window started",
                            level="WARNING",
                            payload={
                                "outage_id": outage_id,
                                "max_market_data_outage_seconds": self.max_market_data_outage_seconds,
                                "attempts": exc.attempts,
                                "error_type": type(exc.__cause__).__name__
                                if exc.__cause__ is not None
                                else type(exc).__name__,
                            },
                        )
                    outage_elapsed = max(0.0, now - outage_started_at)
                    self.monitor.record_event(
                        "market_data_unavailable_observation",
                        "Market-data observation unavailable; no inference performed",
                        level="WARNING",
                        payload={
                            "outage_id": outage_id,
                            "outage_seconds": round(outage_elapsed, 3),
                            "attempts": exc.attempts,
                            "error_type": type(exc.__cause__).__name__
                            if exc.__cause__ is not None
                            else type(exc).__name__,
                            "inference_performed": False,
                            "stale_data_used": False,
                            "order_submission": "disabled",
                        },
                    )
                    self.monitor.heartbeat()
                    if outage_elapsed >= self.max_market_data_outage_seconds:
                        return stop_for_transport_outage(
                            "market_data_outage_budget_exhausted", outage_elapsed
                        )
                    remaining_run = (
                        self.monitor.duration_seconds - self.monitor.elapsed_seconds
                    )
                    remaining_outage = (
                        self.max_market_data_outage_seconds - outage_elapsed
                    )
                    wait_seconds = min(
                        self.observation_interval_seconds,
                        remaining_run,
                        remaining_outage,
                    )
                    if wait_seconds > 0:
                        sleep(wait_seconds)
                    continue

                observation = self._validate_observation(raw_observation)
                if outage_started_at is not None:
                    outage_elapsed = max(0.0, self.clock() - outage_started_at)
                    if outage_elapsed >= self.max_market_data_outage_seconds:
                        return stop_for_transport_outage(
                            "market_data_outage_budget_exhausted", outage_elapsed
                        )
                    clear_deadline = getattr(
                        self.observation_provider,
                        "set_market_data_outage_deadline",
                        None,
                    )
                    if callable(clear_deadline):
                        clear_deadline(None)
                    self.monitor.record_event(
                        "market_data_outage_recovered",
                        "Fresh market data recovered; normal read-only observations resumed",
                        level="WARNING",
                        payload={
                            "outage_id": outage_id,
                            "outage_seconds": round(outage_elapsed, 3),
                            "market_data_request_attempts": (
                                observation.health.market_data_request_attempts
                            ),
                            "inference_performed_after_recovery": True,
                            "stale_data_used": False,
                        },
                    )
                    outage_started_at = None
                    outage_id = None

                request_attempts = observation.health.market_data_request_attempts
                request_exhausted = observation.health.market_data_request_exhausted
                if type(request_attempts) is not int or request_attempts < 1:
                    raise ValueError(
                        "market_data_request_attempts must be a positive integer"
                    )
                if type(request_exhausted) is not bool:
                    raise ValueError("market_data_request_exhausted must be boolean")
                if request_exhausted or request_attempts > 1:
                    self.monitor.record_event(
                        "market_data_retry_exhausted"
                        if request_exhausted
                        else "market_data_retry_recovered",
                        "Market-data transport retry budget exhausted"
                        if request_exhausted
                        else "Market-data transport recovered after retry",
                        level="ERROR" if request_exhausted else "WARNING",
                        payload={
                            "attempts": request_attempts,
                            "error_type": observation.decision.get(
                                "market_data_error_type"
                            ),
                        },
                    )
                stop = stop_controller.evaluate(observation.health)
                self.monitor.record_event(
                    "shadow_observation",
                    "Read-only Paper soak shadow observation recorded",
                    level="ERROR" if stop.stopped else "INFO",
                    payload={
                        "health": asdict(observation.health),
                        "hypothetical_decision": dict(observation.decision),
                        "stop_triggered": stop.stopped,
                        "stop_reason": stop.reason,
                    },
                )
                self.monitor.heartbeat()

                if stop.stopped:
                    self.monitor.finish(
                        status="failed",
                        reason=stop.reason or "shadow_health_stop",
                    )
                    return run_id

                remaining = self.monitor.duration_seconds - self.monitor.elapsed_seconds
                if remaining <= 0:
                    break
                sleep(min(self.observation_interval_seconds, remaining))

            if outage_started_at is not None:
                outage_elapsed = max(0.0, self.clock() - outage_started_at)
                return stop_for_transport_outage(
                    "market_data_unavailable_at_duration_end", outage_elapsed
                )
            self.monitor.finish(status="completed", reason="duration_elapsed")
            return run_id
        except Exception as exc:
            try:
                self.monitor.record_event(
                    "shadow_runner_error",
                    "Paper soak shadow runner failed",
                    level="ERROR",
                    payload={"error_type": type(exc).__name__},
                )
                self.monitor.finish(status="failed", reason="shadow_runner_error")
            except Exception:
                # Preserve the original exception; an active DB run is evidence
                # that terminal persistence itself may need investigation.
                pass
            raise
