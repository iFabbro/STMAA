import json
import sqlite3

import pytest

from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils import paper_soak_stop_controller
from src.utils.paper_soak_stop_controller import (
    MAX_CONSECUTIVE_API_FAILURES,
    MAX_HEARTBEAT_AGE_SECONDS,
    PaperSoakHealthSnapshot,
    PaperSoakStopController,
    StopPersistenceError,
)


def healthy_snapshot(**overrides):
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


def make_controller(tmp_path):
    database = tmp_path / "paper-soak.db"
    monitor = PaperSoakMonitor(db_path=database)
    monitor.start()
    return monitor, PaperSoakStopController(monitor)


def test_healthy_snapshot_does_not_stop(tmp_path):
    _, controller = make_controller(tmp_path)

    decision = controller.evaluate(healthy_snapshot())

    assert decision.stopped is False
    assert decision.reason is None
    assert controller.stopped is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("broker_endpoint_is_paper", False, "non_paper_broker_endpoint"),
        ("order_mutation_attempted", True, "broker_order_mutation_attempted"),
        ("broker_state_consistent", False, "broker_state_mismatch"),
        ("exposure_determinable", False, "exposure_not_determinable"),
        ("market_data_valid", False, "market_data_invalid"),
        ("market_data_fresh", False, "market_data_stale"),
        ("persistence_healthy", False, "persistence_or_lock_failure"),
        ("broker_snapshot_complete", False, "broker_snapshot_incomplete"),
    ],
)
def test_each_boolean_safety_violation_triggers_stop(tmp_path, field, value, reason):
    _, controller = make_controller(tmp_path)

    decision = controller.evaluate(healthy_snapshot(**{field: value}))

    assert decision.stopped is True
    assert decision.reason == reason
    assert controller.stopped is True


def test_api_failure_threshold_is_exact(tmp_path):
    _, controller = make_controller(tmp_path)

    assert controller.evaluate(
        healthy_snapshot(api_consecutive_failures=MAX_CONSECUTIVE_API_FAILURES - 1)
    ).stopped is False
    decision = controller.evaluate(
        healthy_snapshot(api_consecutive_failures=MAX_CONSECUTIVE_API_FAILURES)
    )

    assert decision.stopped is True
    assert decision.reason == "broker_api_unreachable_threshold"


def test_heartbeat_age_threshold_is_strictly_greater_than_limit(tmp_path):
    _, controller = make_controller(tmp_path)

    assert controller.evaluate(
        healthy_snapshot(heartbeat_age_seconds=MAX_HEARTBEAT_AGE_SECONDS)
    ).stopped is False
    decision = controller.evaluate(
        healthy_snapshot(heartbeat_age_seconds=MAX_HEARTBEAT_AGE_SECONDS + 0.001)
    )

    assert decision.stopped is True
    assert decision.reason == "heartbeat_stale_threshold"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("broker_endpoint_is_paper", None),
        ("order_mutation_attempted", None),
        ("broker_state_consistent", None),
        ("exposure_determinable", None),
        ("market_data_valid", None),
        ("market_data_fresh", None),
        ("persistence_healthy", None),
        ("broker_snapshot_complete", None),
        ("api_consecutive_failures", None),
        ("heartbeat_age_seconds", None),
        ("api_consecutive_failures", -1),
        ("api_consecutive_failures", True),
        ("heartbeat_age_seconds", float("nan")),
        ("heartbeat_age_seconds", float("inf")),
        ("heartbeat_age_seconds", -1),
    ],
)
def test_missing_or_invalid_health_signal_fails_closed(tmp_path, field, value):
    _, controller = make_controller(tmp_path)

    decision = controller.evaluate(healthy_snapshot(**{field: value}))

    assert decision.stopped is True
    assert decision.reason.startswith("unknown_or_invalid_signal:")


def test_stop_is_sticky_and_later_healthy_snapshot_cannot_resume(tmp_path):
    monitor, controller = make_controller(tmp_path)
    triggered = controller.evaluate(
        healthy_snapshot(market_data_fresh=False)
    )
    later = controller.evaluate(healthy_snapshot())

    assert triggered.stopped is True
    assert triggered.reason == "market_data_stale"
    assert later.stopped is True
    assert later.reason == "stop_already_latched"

    with sqlite3.connect(monitor.db_path) as conn:
        rows = conn.execute(
            "SELECT event_type, payload FROM events WHERE run_id = ? "
            "AND event_type = 'stop_triggered'",
            (monitor.run_id,),
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][1])["reason"] == "market_data_stale"


def test_stop_is_restored_from_event_log_after_controller_recreation(tmp_path):
    monitor, controller = make_controller(tmp_path)
    controller.evaluate(healthy_snapshot(exposure_determinable=False))

    restored = PaperSoakStopController(monitor)

    assert restored.stopped is True
    decision = restored.evaluate(healthy_snapshot())
    assert decision.stopped is True
    assert decision.reason == "stop_already_latched"


def test_stop_remains_latched_in_memory_if_event_persistence_fails(tmp_path, monkeypatch):
    monitor, controller = make_controller(tmp_path)

    def fail_log_event(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated disk failure")

    monkeypatch.setattr(paper_soak_stop_controller, "log_event", fail_log_event)
    with pytest.raises(StopPersistenceError, match="durable stop event"):
        controller.evaluate(healthy_snapshot(persistence_healthy=False))

    assert controller.stopped is True
    assert controller.evaluate(healthy_snapshot()).reason == "stop_already_latched"


def test_non_snapshot_input_fails_closed(tmp_path):
    _, controller = make_controller(tmp_path)

    decision = controller.evaluate(None)

    assert decision.stopped is True
    assert decision.reason == "unknown_or_invalid_signal:snapshot"
