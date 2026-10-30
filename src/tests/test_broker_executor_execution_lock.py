"""Concurrency regressions for the broker execution critical section."""
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from threading import Event, Lock

import pytest

from src.execution import broker_executor
from src.execution.broker_executor import BrokerExecutor


def _executor(state_file):
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state_file = state_file
    return executor


def _hold_execution_in_child(state_file, entered, release):
    executor = _executor(state_file)

    def hold_execution(weights, min_notional):
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("timed out waiting to release child execution lock")
        return "child"

    executor._execute_weights_locked = hold_execution
    executor.execute_weights(object())


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


def test_execution_lock_serializes_separate_processes(tmp_path):
    state_file = tmp_path / "broker_state.json"
    context = get_context("spawn")
    child_entered = context.Event()
    release_child = context.Event()
    child = context.Process(
        target=_hold_execution_in_child,
        args=(state_file, child_entered, release_child),
    )
    second_entered = Event()
    second = _executor(state_file)

    def second_execution(weights, min_notional):
        second_entered.set()
        return "parent"

    second._execute_weights_locked = second_execution
    child.start()
    try:
        assert child_entered.wait(timeout=10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            second_future = pool.submit(second.execute_weights, object())
            # The parent process must wait for the child process' OS lock.
            assert not second_entered.wait(timeout=0.3)
            release_child.set()
            assert second_future.result(timeout=10) == "parent"
    finally:
        release_child.set()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)

    assert child.exitcode == 0


def test_execution_lock_acquisition_failure_fails_closed(tmp_path, monkeypatch):
    executor = _executor(tmp_path / "broker_state.json")
    execution_called = False

    def forbidden_execution(weights, min_notional):
        nonlocal execution_called
        execution_called = True
        return "unsafe"

    def fail_lock(*args, **kwargs):
        raise OSError("simulated flock failure")

    executor._execute_weights_locked = forbidden_execution
    monkeypatch.setattr(broker_executor.fcntl, "flock", fail_lock)

    with pytest.raises(RuntimeError, match="Unable to acquire broker execution lock"):
        executor.execute_weights(object())

    assert execution_called is False



def test_direct_execution_lock_serializes_executor_execution(tmp_path):
    state_file = tmp_path / "broker_state.json"
    smoke = _executor(state_file)
    strategy = _executor(state_file)
    smoke_entered = Event()
    release_smoke = Event()
    strategy_entered = Event()

    def hold_smoke_preflight_and_submission():
        with smoke.execution_lock():
            smoke_entered.set()
            assert release_smoke.wait(timeout=5)

    def strategy_execution(weights, min_notional):
        strategy_entered.set()
        return "strategy"

    strategy._execute_weights_locked = strategy_execution

    with ThreadPoolExecutor(max_workers=2) as pool:
        smoke_future = pool.submit(hold_smoke_preflight_and_submission)
        assert smoke_entered.wait(timeout=5)
        strategy_future = pool.submit(strategy.execute_weights, object())
        # A direct Paper smoke path must exclude the normal executor path.
        assert not strategy_entered.wait(timeout=0.2)
        release_smoke.set()
        smoke_future.result(timeout=5)
        assert strategy_future.result(timeout=5) == "strategy"

    assert strategy_entered.is_set()
