import json

from src.utils.paper_soak_report import build_report
from src.utils.sqlite_logger import end_run, log_event, start_run


HEALTHY = {
    "broker_endpoint_is_paper": True,
    "order_mutation_attempted": False,
    "broker_state_consistent": True,
    "exposure_determinable": True,
    "market_data_valid": True,
    "market_data_fresh": True,
    "persistence_healthy": True,
    "broker_snapshot_complete": True,
    "api_consecutive_failures": 0,
    "heartbeat_age_seconds": 1.0,
}
COMMIT_SHA = "a" * 40
GREEN_CI = {
    "commit_sha": COMMIT_SHA,
    "workflows": {
        "CI": "success",
        "Tests": "success",
        "Lint": "success",
        "Secrets scan": "success",
    },
}


def create_run(database, *, metadata=None, status="completed", observation=True):
    run_id = start_run(
        mode="paper_soak_read_only",
        branch="main",
        commit_sha=COMMIT_SHA,
        meta={
            "duration_seconds": 3,
            "heartbeat_interval_seconds": 1,
            "order_submission": "disabled",
            "provider_name": "STMAA read-only provider",
            "provider_kind": "production_read_only",
            "production_strategy_inference": True,
            **(metadata or {}),
        },
        db_path=database,
    )
    log_event(
        run_id,
        source="paper_soak_monitor",
        event_type="session_started",
        message="started",
        db_path=database,
    )
    if observation:
        for sequence in range(1, 4):
            log_event(
                run_id,
                source="paper_soak_shadow_runner",
                event_type="shadow_observation",
                message="observation",
                payload={
                    "health": HEALTHY,
                    "hypothetical_decision": {"action": "hold"},
                    "stop_triggered": False,
                },
                db_path=database,
            )
            log_event(
                run_id,
                source="paper_soak_monitor",
                event_type="heartbeat",
                message="heartbeat",
                payload={"sequence": sequence, "elapsed_seconds": sequence - 1},
                db_path=database,
            )
    if status == "completed":
        log_event(
            run_id,
            source="paper_soak_monitor",
            event_type="session_finished",
            message="finished",
            payload={
                "status": "completed",
                "elapsed_seconds": 3,
                "heartbeat_count": 3 if observation else 0,
            },
            db_path=database,
        )
        end_run(run_id, status="completed", db_path=database)
    elif status == "failed":
        log_event(
            run_id,
            source="paper_soak_monitor",
            event_type="session_failed",
            message="failed",
            level="ERROR",
            payload={"status": "failed"},
            db_path=database,
        )
        end_run(run_id, status="failed", db_path=database)
    return run_id


def write_ci_evidence(tmp_path, evidence=None):
    path = tmp_path / "ci-evidence.json"
    path.write_text(json.dumps(evidence or GREEN_CI), encoding="utf-8")
    return path


def test_missing_database_is_not_run(tmp_path):
    report = build_report(tmp_path / "missing.db")
    assert report["overall_status"] == "NOT_RUN"
    assert report["soak_status"] == "NOT_RUN"


def test_no_matching_run_is_not_run(tmp_path):
    report = build_report(tmp_path / "empty.db")
    assert report["overall_status"] == "NOT_RUN"


def test_active_run_is_incomplete(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database, status="running")
    report = build_report(database)
    assert report["overall_status"] == "INCOMPLETE"
    assert report["checks"]["run_completed"] is False


def test_failed_run_fails_report(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database, status="failed")
    report = build_report(database)
    assert report["overall_status"] == "FAIL"
    assert "run_status_failed" in report["failures"]


def test_missing_observation_is_incomplete_not_pass(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database, observation=False)
    report = build_report(database)
    assert report["overall_status"] == "INCOMPLETE"
    assert report["checks"]["observations_present"] is False


def test_unhealthy_observation_fails_closed(tmp_path):
    database = tmp_path / "soak.db"
    run_id = create_run(database)
    log_event(
        run_id,
        source="paper_soak_shadow_runner",
        event_type="shadow_observation",
        message="unsafe observation",
        payload={
            "health": {**HEALTHY, "exposure_determinable": False},
            "stop_triggered": True,
            "hypothetical_decision": {},
        },
        db_path=database,
    )
    report = build_report(database)
    assert report["overall_status"] == "FAIL"
    assert "stop_triggered" in report["failures"]
    assert "observation_3_failed_safety_control" in report["failures"]


def test_unknown_mandatory_signal_fails_closed(tmp_path):
    database = tmp_path / "soak.db"
    run_id = create_run(database)
    log_event(
        run_id,
        source="paper_soak_shadow_runner",
        event_type="shadow_observation",
        message="incomplete observation",
        payload={
            "health": {
                key: value
                for key, value in HEALTHY.items()
                if key != "market_data_fresh"
            },
            "stop_triggered": False,
            "hypothetical_decision": {},
        },
        db_path=database,
    )
    report = build_report(database)
    assert report["overall_status"] == "FAIL"
    assert "observation_3_unknown_signal:market_data_fresh" in report["failures"]


def test_provider_placeholder_cannot_pass_operational_acceptance(tmp_path):
    database = tmp_path / "soak.db"
    create_run(
        database,
        metadata={
            "provider_name": None,
            "provider_kind": "injected_test",
            "production_strategy_inference": False,
        },
    )
    report = build_report(database, ci_evidence_path=write_ci_evidence(tmp_path))
    assert report["soak_status"] == "INCOMPLETE"
    assert report["overall_status"] == "INCOMPLETE"
    assert report["checks"]["production_read_only_provider_declared"] is False


def test_missing_ci_evidence_cannot_pass_report(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database)
    report = build_report(database)
    assert report["overall_status"] == "INCOMPLETE"
    assert report["ci"]["status"] == "NOT_CAPTURED"


def test_complete_evidence_passes_and_matches_ci_commit(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database)
    ci_path = write_ci_evidence(tmp_path)
    report = build_report(database, ci_evidence_path=ci_path)
    assert report["overall_status"] == "PASS"
    assert report["soak_status"] == "PASS"
    assert report["checks"]["duration_met"] is True
    assert report["checks"]["heartbeat_and_observation_coverage_sufficient"] is True
    assert report["ci"]["status"] == "PASS"
    assert report["ci"]["soak_commit_sha"] == COMMIT_SHA


def test_insufficient_heartbeat_coverage_is_incomplete(tmp_path):
    database = tmp_path / "soak.db"
    run_id = create_run(database)
    # Remove the final sample and heartbeat while retaining a claimed duration.
    from src.utils.sqlite_logger import db_conn

    with db_conn(database) as conn:
        conn.execute(
            "DELETE FROM events WHERE id = (SELECT MAX(id) FROM events "
            "WHERE run_id = ? AND event_type = 'heartbeat')",
            (run_id,),
        )
    report = build_report(database, ci_evidence_path=write_ci_evidence(tmp_path))
    assert report["overall_status"] == "INCOMPLETE"
    assert report["checks"]["heartbeat_and_observation_coverage_sufficient"] is False


def test_ci_evidence_for_different_commit_fails(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database)
    mismatched = {**GREEN_CI, "commit_sha": "b" * 40}
    report = build_report(
        database,
        ci_evidence_path=write_ci_evidence(tmp_path, mismatched),
    )
    assert report["overall_status"] == "FAIL"
    assert "ci_commit_sha_mismatch" in report["failures"]


def test_failed_ci_workflow_fails_report(tmp_path):
    database = tmp_path / "soak.db"
    create_run(database)
    failed_ci = {
        **GREEN_CI,
        "workflows": {**GREEN_CI["workflows"], "Tests": "failure"},
    }
    report = build_report(
        database,
        ci_evidence_path=write_ci_evidence(tmp_path, failed_ci),
    )
    assert report["overall_status"] == "FAIL"
    assert "ci_workflow_failed:Tests" in report["failures"]


def test_report_can_select_a_specific_run(tmp_path):
    database = tmp_path / "soak.db"
    selected = create_run(database)
    create_run(database, status="failed")
    report = build_report(database, selected, write_ci_evidence(tmp_path))
    assert report["run_id"] == selected
    assert report["overall_status"] == "PASS"
