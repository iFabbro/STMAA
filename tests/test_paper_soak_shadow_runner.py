import json

import pytest

from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils.paper_soak_shadow_runner import (
    PaperSoakShadowObservation,
    PaperSoakShadowRunner,
)
from src.utils.paper_soak_stop_controller import PaperSoakHealthSnapshot
from src.utils.sqlite_logger import db_conn


class FakeClock:
    def __init__(self):
        self.value = 50.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def healthy_health(**overrides):
    values = {
        "broker_endpoint_is_paper": True,
        "order_mutation_attempted": False,
        "broker_state_consistent": True,
        "exposure_determinable": True,
        "market_data_valid": True,
        "market_data_fresh": True,
        "persistence_healthy": True,
        "broker_snapshot_complete": True,
        "api_consecutive_failures": 0,
        "heartbeat_age_seconds": 0.0,
    }
    values.update(overrides)
    return PaperSoakHealthSnapshot(**values)


def test_shadow_runner_records_hypothetical_decisions_until_duration(tmp_path):
    database = tmp_path / "shadow.db"
    clock = FakeClock()
    monitor = PaperSoakMonitor(
        db_path=database,
        duration_seconds=2.5,
        heartbeat_interval_seconds=1,
        clock=clock,
    )
    observations = []

    def provider():
        observations.append(clock())
        return PaperSoakShadowObservation(
            health=healthy_health(),
            decision={"weights": {"AAPL": 0.1}, "action": "hold"},
        )

    runner = PaperSoakShadowRunner(
        monitor,
        provider,
        observation_interval_seconds=1,
    )
    run_id = runner.run(sleep=clock.sleep)

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT status, mode FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, payload FROM events WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    assert run["status"] == "completed"
    assert run["mode"] == "paper_soak_read_only"
    assert observations == [50.0, 51.0, 52.0]
    shadow_events = [row for row in events if row["event_type"] == "shadow_observation"]
    assert len(shadow_events) == 3
    first_payload = json.loads(shadow_events[0]["payload"])
    assert first_payload["hypothetical_decision"] == {
        "weights": {"AAPL": 0.1},
        "action": "hold",
    }
    assert first_payload["stop_triggered"] is False
    assert [row["event_type"] for row in events].count("heartbeat") == 3
    assert "BrokerExecutor" not in __import__(
        "src.utils.paper_soak_shadow_runner", fromlist=["x"]
    ).__dict__


def test_shadow_runner_stops_and_marks_run_failed_on_unhealthy_observation(tmp_path):
    database = tmp_path / "shadow.db"
    monitor = PaperSoakMonitor(db_path=database, duration_seconds=10)
    provider_calls = []

    def provider():
        provider_calls.append(True)
        return PaperSoakShadowObservation(
            health=healthy_health(market_data_fresh=False),
            decision={"weights": {"AAPL": 0.0}},
        )

    runner = PaperSoakShadowRunner(monitor, provider)
    run_id = runner.run(sleep=lambda _seconds: pytest.fail("must stop immediately"))

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        stop = conn.execute(
            "SELECT payload FROM events WHERE run_id = ? AND event_type = 'stop_triggered'",
            (run_id,),
        ).fetchone()

    assert run["status"] == "failed"
    assert len(provider_calls) == 1
    assert json.loads(stop["payload"])["reason"] == "market_data_stale"


@pytest.mark.parametrize(
    "decision",
    [
        None,
        float("nan"),
        {"weight": float("inf")},
        {"not_json": object()},
    ],
)
def test_shadow_runner_rejects_invalid_decision_payload(tmp_path, decision):
    monitor = PaperSoakMonitor(db_path=tmp_path / "shadow.db", duration_seconds=1)
    runner = PaperSoakShadowRunner(
        monitor,
        lambda: PaperSoakShadowObservation(
            health=healthy_health(),
            decision=decision,
        ),
    )

    with pytest.raises((TypeError, ValueError)):
        runner.run()

    with db_conn(monitor.db_path) as conn:
        run = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (monitor.run_id,)
        ).fetchone()
    assert run["status"] == "failed"


@pytest.mark.parametrize("interval", [0, -1, True, float("nan"), float("inf")])
def test_shadow_runner_rejects_invalid_observation_interval(tmp_path, interval):
    monitor = PaperSoakMonitor(db_path=tmp_path / "shadow.db")
    with pytest.raises(ValueError, match="positive and finite"):
        PaperSoakShadowRunner(monitor, lambda: None, interval)
