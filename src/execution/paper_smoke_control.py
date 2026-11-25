"""Fail-closed controls for explicitly bounded Alpaca Paper smoke scripts."""
from __future__ import annotations

import signal
import threading
from typing import Any


def install_stop_signal_handlers(stop_event: threading.Event) -> None:
    """Latch SIGINT/SIGTERM without interrupting in-flight order reconciliation."""
    if not isinstance(stop_event, threading.Event):
        raise TypeError("stop_event must be a threading.Event")

    def _latch_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _latch_stop)
    signal.signal(signal.SIGTERM, _latch_stop)


def submit_paper_smoke_order(executor: Any, request: Any) -> Any:
    """Check the executor's Paper identity and stop latch immediately before submit."""
    executor._assert_paper_mutation_allowed()
    return executor.trading_client.submit_order(order_data=request)
