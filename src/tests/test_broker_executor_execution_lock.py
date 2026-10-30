"""Concurrency regressions for the broker execution critical section."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from src.execution.broker_executor import BrokerExecutor


def _executor(state_file):
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state_file = state_file
    return executor


def test_concurrent_executor_instances_share_one_execution_lock(tmp_path):
    state_file = tmp_path / "broker_state.json"
    first = _executor(state_file)
    second = _executor(state_file)
    first_entered = Event()
    second_entered = Event()
    release_first = Event()
    counters_lock = Lock()
    active = 0
    maximum_active = 0

    def first_execution(weights, min_notional):
        nonlocal active, maximum_active
        with counters_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        first_entered.set()
        assert release_first.wait(timeout=5)
        with counters_lock:
            active -= 1
        return "first"

    def second_execution(weights, min_notional):
        nonlocal active, maximum_active
        with counters_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        second_entered.set()
        with counters_lock:
            active -= 1
        return "second"

    first._execute_weights_locked = first_execution
    second._execute_weights_locked = second_execution

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first.execute_weights, object())
        assert first_entered.wait(timeout=5)
        second_future = pool.submit(second.execute_weights, object())
        # The second executor must not enter its broker execution body while
        # the first instance owns the shared, path-scoped OS file lock.
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        assert first_future.result(timeout=5) == "first"
        assert second_future.result(timeout=5) == "second"

    assert maximum_active == 1


def test_execution_lock_is_released_when_execution_raises(tmp_path):
    state_file = tmp_path / "broker_state.json"
    failing = _executor(state_file)
    succeeding = _executor(state_file)
    entered = Event()

    def fail_execution(weights, min_notional):
        raise RuntimeError("simulated execution failure")

    def succeed_execution(weights, min_notional):
        entered.set()
        return "recovered"

    failing._execute_weights_locked = fail_execution
    succeeding._execute_weights_locked = succeed_execution

    with pytest.raises(RuntimeError, match="simulated execution failure"):
        failing.execute_weights(object())

    assert succeeding.execute_weights(object()) == "recovered"
    assert entered.is_set()
