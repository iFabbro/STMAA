import json
import sqlite3

import pytest

from src.utils import paper_soak_monitor
from src.utils.sqlite_logger import db_conn
from src.utils.paper_soak_monitor import PaperSoakMonitor


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), True, "bad"])
def test_monitor_rejects_invalid_duration(duration, tmp_path):
    with pytest.raises(ValueError, match="duration_seconds"):
        PaperSoakMonitor(db_path=tmp_path / "monitor.db", duration_seconds=duration)


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf"), True])
def test_monitor_rejects_invalid_heartbeat_interval(interval, tmp_path):
    with pytest.raises(ValueError, match="heartbeat_interval_seconds"):
        PaperSoakMonitor(
            db_path=tmp_path / "monitor.db",
            heartbeat_interval_seconds=interval,
        )


def test_heartbeat_requires_started_session(tmp_path):
    monitor = PaperSoakMonitor(db_path=tmp_path / "monitor.db")

    with pytest.raises(RuntimeError, match="has not started"):
        monitor.heartbeat()


def test_run_records_durable_session_and_periodic_heartbeats(tmp_path):
    database = tmp_path / "paper-soak.db"
    clock = FakeClock()
    monitor = PaperSoakMonitor(
        db_path=database,
        duration_seconds=2.5,
        heartbeat_interval_seconds=1,
        clock=clock,
    )

    run_id = monitor.run(sleep=clock.sleep)

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, level, payload FROM events "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    assert run["mode"] == "paper_soak_read_only"
    assert run["status"] == "completed"
    metadata = json.loads(run["meta"])
    assert metadata["broker_access"] == "disabled"
    assert metadata["order_submission"] == "disabled"
    assert metadata["duration_seconds"] == 2.5
    assert [event["event_type"] for event in events] == [
        "session_started",
        "heartbeat",
        "heartbeat",
        "heartbeat",
        "session_finished",
    ]
    heartbeat_payloads = [
        json.loads(event["payload"])
        for event in events
        if event["event_type"] == "heartbeat"
    ]
    assert [item["sequence"] for item in heartbeat_payloads] == [1, 2, 3]
    assert [item["elapsed_seconds"] for item in heartbeat_payloads] == [0.0, 1.0, 2.0]
    finish_payload = json.loads(events[-1]["payload"])
    assert finish_payload["status"] == "completed"
    assert finish_payload["heartbeat_count"] == 3


def test_monitor_marks_session_failed_when_loop_raises(tmp_path):
    database = tmp_path / "paper-soak.db"
    clock = FakeClock()
    monitor = PaperSoakMonitor(
        db_path=database,
        duration_seconds=10,
        heartbeat_interval_seconds=1,
        clock=clock,
    )

    def fail_sleep(_seconds):
        raise RuntimeError("simulated scheduler failure")

    with pytest.raises(RuntimeError, match="simulated scheduler failure"):
        monitor.run(sleep=fail_sleep)

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (monitor.run_id,)
        ).fetchone()
        event_types = [
            row["event_type"]
            for row in conn.execute(
                "SELECT event_type FROM events WHERE run_id = ? ORDER BY id",
                (monitor.run_id,),
            )
        ]

    assert run["status"] == "failed"
    assert event_types == [
        "session_started",
        "heartbeat",
        "monitor_error",
        "session_failed",
    ]


def test_failed_heartbeat_does_not_advance_sequence(tmp_path, monkeypatch):
    monitor = PaperSoakMonitor(db_path=tmp_path / "paper-soak.db")
    monitor.start()

    def fail_log_event(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated event-store failure")

    monkeypatch.setattr(paper_soak_monitor, "log_event", fail_log_event)
    with pytest.raises(sqlite3.OperationalError, match="event-store failure"):
        monitor.heartbeat()

    assert monitor._heartbeat_sequence == 0


def test_monitor_rejects_events_after_session_is_finalized(tmp_path):
    monitor = PaperSoakMonitor(db_path=tmp_path / "paper-soak.db")
    monitor.start()
    monitor.finish()

    with pytest.raises(ValueError, match="not active"):
        monitor.heartbeat()


def test_monitor_persists_provider_and_source_identity(tmp_path):
    database = tmp_path / "paper-soak.db"
    monitor = PaperSoakMonitor(
        db_path=database,
        branch="main",
        commit_sha="a" * 40,
        run_metadata={
            "provider_name": "read-only integration",
            "provider_kind": "production_read_only",
            "production_strategy_inference": True,
        },
    )
    run_id = monitor.start()

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT branch, commit_sha, meta FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    metadata = json.loads(run["meta"])
    assert run["branch"] == "main"
    assert run["commit_sha"] == "a" * 40
    assert metadata["provider_name"] == "read-only integration"
    assert metadata["provider_kind"] == "production_read_only"
    assert metadata["production_strategy_inference"] is True
    assert metadata["order_submission"] == "disabled"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"commit_sha": "not-a-sha"}, "40-character"),
        ({"branch": " "}, "branch"),
        ({"run_metadata": {"order_submission": "enabled"}}, "reserved fields"),
        ({"run_metadata": {"invalid": float("nan")}}, "finite JSON"),
    ],
)
def test_monitor_rejects_invalid_run_identity_or_metadata(
    tmp_path, kwargs, message
):
    with pytest.raises((TypeError, ValueError), match=message):
        PaperSoakMonitor(db_path=tmp_path / "paper-soak.db", **kwargs)
