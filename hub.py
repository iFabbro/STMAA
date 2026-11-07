#!/usr/bin/env python3
"""STMAA read-only Textual dashboard entry point.

The module retains the small monitoring and run-lifecycle API consumed by
existing STMAA components and tests. It does not connect to a broker.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from src.utils.monitoring_state import HUB_STATE_PATH
from src.utils.sqlite_logger import end_run, log_event, start_run

RUN_ID: str | None = None
HUB_STATE = HUB_STATE_PATH


def load_state() -> dict[str, Any]:
    """Load the persisted dashboard snapshot; invalid or absent data is unknown."""
    try:
        state = json.loads(Path(HUB_STATE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _finite_number(value: Any) -> float | None:
    """Accept finite JSON numeric primitives, excluding booleans and strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _valid_curve(value: Any) -> list[float]:
    """Validate an entire persisted equity curve without coercing its values."""
    if not isinstance(value, list):
        return []
    numbers = [_finite_number(item) for item in value]
    if any(item is None for item in numbers):
        return []
    return [float(item) for item in numbers]


def render() -> None:
    """Run the Textual dashboard; its current providers are intentionally absent."""
    from src.dashboard import STMAADashboard

    STMAADashboard().run()


def main() -> None:
    """Track the dashboard process lifecycle and always finalize its audit run."""
    global RUN_ID
    RUN_ID = start_run(mode="hub", meta={"component": "hub", "access": "read-only"})
    status = "failed"
    try:
        log_event(RUN_ID, "hub", "startup", "Read-only Textual dashboard started")
        render()
        status = "completed"
    except KeyboardInterrupt:
        status = "completed"
    finally:
        run_id = RUN_ID
        RUN_ID = None
        if run_id is not None:
            end_run(run_id, status=status)


if __name__ == "__main__":
    main()
