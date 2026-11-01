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

from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils.paper_soak_stop_controller import (
    PaperSoakHealthSnapshot,
    PaperSoakStopController,
)

DEFAULT_OBSERVATION_INTERVAL_SECONDS = 60.0


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
        self.monitor = monitor
        self.observation_provider = observation_provider
        self.observation_interval_seconds = interval

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
        try:
            while self.monitor.elapsed_seconds < self.monitor.duration_seconds:
                observation = self._validate_observation(
                    self.observation_provider()
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
