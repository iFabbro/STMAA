"""Process-crash and concurrent-maintenance regression tests for PPO artifacts."""

import json
import multiprocessing
import os
import time
from pathlib import Path

from src.agents.ppo_agent import PPOAgent, _artifact_lock


def _hold_artifact_lock_until_terminated(model_path, acquired):
    with _artifact_lock(Path(model_path), timeout_seconds=5):
        acquired.set()
        while True:
            time.sleep(1)


def _acquire_artifact_lock_after_crash(model_path, result_queue):
    try:
        with _artifact_lock(Path(model_path), timeout_seconds=2):
            result_queue.put("acquired")
    except Exception as exc:  # pragma: no cover - reported to the parent process
        result_queue.put(f"error:{type(exc).__name__}:{exc}")


def _prune_artifacts_worker(model_path, ready, start, result_queue):
    agent = PPOAgent(
        tickers=["A"], model_path=model_path, load_existing_model=False
    )
    ready.set()
    if not start.wait(timeout=5):
        result_queue.put(("error", "start barrier timed out"))
        return
    try:
        removed = agent.prune_artifacts(
            retain_generations=1,
            dry_run=False,
            min_generation_age_seconds=0,
            min_temp_age_seconds=0,
        )
        result_queue.put(("success", removed))
    except Exception as exc:  # pragma: no cover - reported to the parent process
        result_queue.put(("error", f"{type(exc).__name__}:{exc}"))


def test_ppo_artifact_lock_is_released_when_owner_process_is_terminated(tmp_path):
    context = multiprocessing.get_context("fork")
    model_path = tmp_path / "terminated-owner.zip"
    acquired = context.Event()
    result_queue = context.Queue()

    owner = context.Process(
        target=_hold_artifact_lock_until_terminated,
        args=(str(model_path), acquired),
    )
    owner.start()
    try:
        assert acquired.wait(timeout=5), "lock owner did not acquire the artifact lock"
        owner.terminate()
        owner.join(timeout=5)
        assert not owner.is_alive(), "terminated lock owner did not exit"

        waiter = context.Process(
            target=_acquire_artifact_lock_after_crash,
            args=(str(model_path), result_queue),
        )
        waiter.start()
        waiter.join(timeout=5)
        assert not waiter.is_alive(), "lock waiter did not finish after owner termination"
        assert waiter.exitcode == 0
        assert result_queue.get(timeout=1) == "acquired"
    finally:
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=2)
        result_queue.close()
        result_queue.join_thread()


def test_concurrent_ppo_prune_processes_are_serialized_and_idempotent(tmp_path):
    context = multiprocessing.get_context("fork")
    model_path = tmp_path / "concurrent-prune.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def __init__(self, payload):
            self.payload = payload

        def save(self, path):
            Path(path).write_bytes(self.payload)

    for payload in (b"generation-one", b"generation-two", b"generation-three"):
        agent.model = SavedModel(payload)
        agent.save()

    manifest_path = Path(str(model_path) + ".metadata.json")
    manifest = json.loads(manifest_path.read_text())
    active_artifact = tmp_path / manifest["artifact_file"]
    all_generations = set(tmp_path.glob("concurrent-prune.*.zip"))
    expected_removed = all_generations - {active_artifact}
    assert len(expected_removed) == 2

    # Make the non-active generations unambiguously eligible for pruning.
    old = time.time() - 10
    for artifact in expected_removed:
        os.utime(artifact, (old, old))

    ready_events = [context.Event(), context.Event()]
    start = context.Event()
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_prune_artifacts_worker,
            args=(str(model_path), ready_events[index], start, result_queue),
        )
        for index in range(2)
    ]

    try:
        for worker in workers:
            worker.start()
        assert all(event.wait(timeout=5) for event in ready_events)
        start.set()

        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive(), "concurrent prune process did not finish"
            assert worker.exitcode == 0

        outcomes = [result_queue.get(timeout=2) for _ in workers]
        assert all(status == "success" for status, _ in outcomes), outcomes
        removed_paths = [
            Path(path)
            for status, paths in outcomes
            for path in paths
        ]
        assert set(removed_paths) == expected_removed
        assert len(removed_paths) == len(set(removed_paths))
        assert active_artifact.is_file()
        assert active_artifact.read_bytes()
        assert json.loads(manifest_path.read_text()) == manifest
        assert set(tmp_path.glob("concurrent-prune.*.zip")) == {active_artifact}
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
        result_queue.close()
        result_queue.join_thread()
