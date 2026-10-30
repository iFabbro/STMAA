"""Concurrency regressions for the broker execution critical section."""
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from threading import Event, Lock

import pytest

from src.execution import broker_executor
from src.execution.broker_executor import BrokerExecutor, _canonicalize_state_file_path


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


def test_execution_lock_is_reentrant_for_account_refresh(tmp_path):
    executor = _executor(tmp_path / "broker_state.json")
    executor.execution_lock_timeout_seconds = 0.1
    executor._get_account_status_locked = lambda: {"account_data_fresh": True}

    # get_account_status must not deadlock when called from execute_weights,
    # which already owns the same execution lock.
    with executor.execution_lock():
        account = executor.get_account_status()

    assert account["account_data_fresh"] is True


def test_account_refresh_waits_for_active_execution_lock(tmp_path):
    executor = _executor(tmp_path / "broker_state.json")
    refresh_entered = Event()
    release_execution = Event()

    def refresh():
        refresh_entered.set()
        return {"account_data_fresh": True}

    executor._get_account_status_locked = refresh

    def hold_execution():
        with executor.execution_lock():
            assert release_execution.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        execution_future = pool.submit(hold_execution)
        # Ensure the first thread owns the execution lock before starting refresh.
        import time
        deadline = time.monotonic() + 5
        while not (executor.state_file.with_name(
            executor.state_file.name + ".execution.lock"
        ).exists()) and time.monotonic() < deadline:
            time.sleep(0.01)
        refresh_future = pool.submit(executor.get_account_status)
        assert not refresh_entered.wait(timeout=0.2)
        release_execution.set()
        execution_future.result(timeout=5)
        assert refresh_future.result(timeout=5)["account_data_fresh"] is True

    assert refresh_entered.is_set()


def _hold_lock_until_terminated(state_file, entered):
    executor = _executor(state_file)
    with executor.execution_lock():
        entered.set()
        import time

        while True:
            time.sleep(0.05)


def test_execution_lock_timeout_fails_closed(tmp_path):
    state_file = tmp_path / "broker_state.json"
    holder = _executor(state_file)
    contender = _executor(state_file)
    contender.execution_lock_timeout_seconds = 0.15
    execution_called = False
    holder_entered = Event()
    release_holder = Event()

    def forbidden_execution(weights, min_notional):
        nonlocal execution_called
        execution_called = True
        return "unsafe"

    def hold():
        with holder.execution_lock():
            holder_entered.set()
            assert release_holder.wait(timeout=5)

    contender._execute_weights_locked = forbidden_execution
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        assert holder_entered.wait(timeout=5)
        with pytest.raises(RuntimeError, match="Timed out acquiring broker execution lock"):
            contender.execute_weights(object())
        release_holder.set()
        future.result(timeout=5)

    assert execution_called is False


def test_execution_lock_recovers_after_process_crash(tmp_path):
    state_file = tmp_path / "broker_state.json"
    context = get_context("spawn")
    child_entered = context.Event()
    child = context.Process(
        target=_hold_lock_until_terminated,
        args=(state_file, child_entered),
    )
    parent = _executor(state_file)
    parent.execution_lock_timeout_seconds = 1.0
    child.start()
    try:
        assert child_entered.wait(timeout=10)
        child.terminate()
        child.join(timeout=10)
        assert child.exitcode is not None and child.exitcode != 0
        with parent.execution_lock():
            pass
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)


def test_invalid_execution_lock_timeout_is_rejected(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("STMAA_BROKER_EXECUTION_LOCK_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValueError, match="finite positive number"):
        BrokerExecutor()



def test_state_file_symlink_aliases_share_canonical_lock_path(tmp_path):
    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir()
    canonical_state = canonical_dir / "broker_state.json"

    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    alias_state = alias_dir / "broker_state.json"
    alias_state.symlink_to(canonical_state)

    canonicalized_target = _canonicalize_state_file_path(canonical_state)
    canonicalized_alias = _canonicalize_state_file_path(alias_state)

    assert canonicalized_alias == canonicalized_target
    assert canonicalized_alias.with_name(
        canonicalized_alias.name + ".execution.lock"
    ) == canonicalized_target.with_name(
        canonicalized_target.name + ".execution.lock"
    )
