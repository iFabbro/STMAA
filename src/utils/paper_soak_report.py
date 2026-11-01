"""Generate a fail-closed, read-only acceptance report for Paper soak runs.

The report evaluates persisted SQLite evidence and a separately supplied CI
evidence JSON file. It never connects to a broker, queries GitHub, or changes
trading state. A CI evidence file is an operator-supplied snapshot, not a
cryptographic attestation.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from src.utils.sqlite_logger import DB_PATH

REPORT_SCHEMA_VERSION = 1
REQUIRED_BOOLEAN_SIGNALS = (
    "broker_endpoint_is_paper",
    "order_mutation_attempted",
    "broker_state_consistent",
    "exposure_determinable",
    "market_data_valid",
    "market_data_fresh",
    "persistence_healthy",
    "broker_snapshot_complete",
)
REQUIRED_CI_WORKFLOWS = ("CI", "Tests", "Lint", "Secrets scan")


def _load_json(value: str | None, *, field: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid JSON in {field}") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return parsed


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _load_ci_evidence(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"_invalid": True}
    return payload if isinstance(payload, dict) else {"_invalid": True}


def _base_report(run_id: str | None) -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "overall_status": "NOT_RUN",
        "soak_status": "NOT_RUN",
        "checks": {},
        "failures": [],
        "limitations": [
            "The report evaluates persisted local evidence and supplied CI evidence only.",
            "CI evidence is an operator-supplied snapshot; verify it against GitHub for the recorded commit.",
            "A declared provider identity is metadata, not an independent security attestation.",
        ],
        "ci": {"status": "NOT_CAPTURED", "commit_sha": None, "soak_commit_sha": None, "workflows": {}},
    }


def build_report(
    db_path: Path = DB_PATH,
    run_id: str | None = None,
    ci_evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Build a report from SQLite and optional CI evidence without modifying either."""
    db_path = Path(db_path)
    report = _base_report(run_id)
    ci_evidence = _load_ci_evidence(ci_evidence_path)
    if ci_evidence is not None:
        if ci_evidence.get("_invalid"):
            report["ci"]["status"] = "FAIL"
            report["failures"].append("ci_evidence_unreadable_or_invalid")
        else:
            workflows = ci_evidence.get("workflows")
            report["ci"] = {
                "status": "INCOMPLETE",
                "commit_sha": ci_evidence.get("commit_sha"),
                "workflows": workflows if isinstance(workflows, dict) else {},
            }

    if not db_path.is_file():
        report["limitations"].append("The event database does not exist.")
        return report

    try:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if run_id:
                run = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ? AND mode = 'paper_soak_read_only'",
                    (run_id,),
                ).fetchone()
            else:
                run = conn.execute(
                    "SELECT * FROM runs WHERE mode = 'paper_soak_read_only' "
                    "ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            if run is None:
                return report
            events = conn.execute(
                "SELECT event_type, level, payload FROM events "
                "WHERE run_id = ? ORDER BY id",
                (run["run_id"],),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        report["overall_status"] = "FAIL"
        report["soak_status"] = "FAIL"
        report["failures"].append(f"event_database_unreadable:{type(exc).__name__}")
        return report

    report["run_id"] = run["run_id"]
    try:
        metadata = _load_json(run["meta"], field="runs.meta")
    except ValueError:
        metadata = {}
        report["failures"].append("malformed_run_metadata")

    parsed_events: list[tuple[str, str, dict[str, Any]]] = []
    for event in events:
        try:
            payload = _load_json(event["payload"], field="events.payload")
        except ValueError:
            report["failures"].append("malformed_event_payload")
            payload = {}
        parsed_events.append((event["event_type"], event["level"], payload))

    stop_events = [item for item in parsed_events if item[0] == "stop_triggered"]
    error_events = [
        item for item in parsed_events
        if item[1] == "ERROR" or item[0] in {
            "monitor_error", "shadow_runner_error", "session_failed"
        }
    ]
    observations = [item[2] for item in parsed_events if item[0] == "shadow_observation"]
    heartbeats = [item[2] for item in parsed_events if item[0] == "heartbeat"]
    finish_events = [item[2] for item in parsed_events if item[0] == "session_finished"]
    finish = finish_events[-1] if finish_events else {}
    duration = metadata.get("duration_seconds")
    interval = metadata.get("heartbeat_interval_seconds")
    elapsed = finish.get("elapsed_seconds")
    target_valid = _finite_number(duration) and duration > 0
    interval_valid = _finite_number(interval) and interval > 0
    elapsed_valid = _finite_number(elapsed) and elapsed >= 0
    duration_met = bool(target_valid and elapsed_valid and elapsed >= duration)
    expected_samples = (
        math.ceil(duration / interval)
        if target_valid and interval_valid
        else None
    )
    heartbeat_count = finish.get("heartbeat_count")
    heartbeat_coverage = bool(
        expected_samples is not None
        and type(heartbeat_count) is int
        and heartbeat_count == len(heartbeats)
        and len(heartbeats) >= expected_samples
        and len(observations) >= expected_samples
    )

    provider_name = metadata.get("provider_name")
    provider_kind = metadata.get("provider_kind")
    production_inference = metadata.get("production_strategy_inference")
    provider_declared = (
        isinstance(provider_name, str) and bool(provider_name.strip())
        and provider_kind == "production_read_only"
        and production_inference is True
    )
    commit_sha = run["commit_sha"] or metadata.get("commit_sha")
    report["ci"]["soak_commit_sha"] = commit_sha
    commit_valid = isinstance(commit_sha, str) and bool(
        re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha)
    )

    ci_workflows = report["ci"].get("workflows", {})
    ci_sha = report["ci"].get("commit_sha")
    ci_sha_matches = bool(
        commit_valid and isinstance(ci_sha, str) and ci_sha.lower() == commit_sha.lower()
    )
    ci_failed = [
        name for name in REQUIRED_CI_WORKFLOWS
        if ci_workflows.get(name) in {"failure", "cancelled", "timed_out", "action_required"}
    ]
    ci_green = bool(
        ci_evidence is not None
        and not ci_evidence.get("_invalid")
        and ci_sha_matches
        and all(ci_workflows.get(name) == "success" for name in REQUIRED_CI_WORKFLOWS)
    )
    if ci_evidence is not None and not ci_evidence.get("_invalid"):
        if ci_failed or (ci_sha and not ci_sha_matches):
            report["ci"]["status"] = "FAIL"
        elif ci_green:
            report["ci"]["status"] = "PASS"
        else:
            report["ci"]["status"] = "INCOMPLETE"

    observation_failures: list[str] = []
    for index, observation in enumerate(observations):
        health = observation.get("health")
        if not isinstance(health, dict):
            observation_failures.append(f"observation_{index}_missing_health")
            continue
        missing = [
            key for key in REQUIRED_BOOLEAN_SIGNALS
            if type(health.get(key)) is not bool
        ]
        if missing:
            observation_failures.extend(
                f"observation_{index}_unknown_signal:{key}" for key in missing
            )
            continue
        failures = health.get("api_consecutive_failures")
        heartbeat_age = health.get("heartbeat_age_seconds")
        if type(failures) is not int or failures < 0:
            observation_failures.append(f"observation_{index}_invalid_api_failures")
            continue
        if not _finite_number(heartbeat_age) or heartbeat_age < 0:
            observation_failures.append(f"observation_{index}_invalid_heartbeat_age")
            continue
        unsafe = (
            not health["broker_endpoint_is_paper"]
            or health["order_mutation_attempted"]
            or not health["broker_state_consistent"]
            or not health["exposure_determinable"]
            or not health["market_data_valid"]
            or not health["market_data_fresh"]
            or not health["persistence_healthy"]
            or not health["broker_snapshot_complete"]
            or failures >= 3
            or heartbeat_age > 120
            or observation.get("stop_triggered") is not False
            or not isinstance(observation.get("hypothetical_decision"), dict)
        )
        if unsafe:
            observation_failures.append(f"observation_{index}_failed_safety_control")

    checks = {
        "run_completed": run["status"] == "completed" and run["ended_at"] is not None,
        "duration_configuration_valid": target_valid,
        "heartbeat_interval_valid": interval_valid,
        "duration_met": duration_met,
        "terminal_event_present": bool(finish_events),
        "heartbeat_and_observation_coverage_sufficient": heartbeat_coverage,
        "no_stop_triggered": not stop_events,
        "no_error_events": not error_events,
        "observations_present": bool(observations),
        "all_observations_safe": bool(observations) and not observation_failures,
        "production_read_only_provider_declared": provider_declared,
        "source_commit_sha_recorded": commit_valid,
        "order_submission_disabled": metadata.get("order_submission") == "disabled",
        "ci_evidence_present": ci_evidence is not None and not (
            ci_evidence and ci_evidence.get("_invalid")
        ),
        "ci_commit_matches_soak": ci_sha_matches,
        "all_required_ci_workflows_green": ci_green,
    }
    report["checks"] = checks
    report["failures"].extend(observation_failures)
    if stop_events:
        report["failures"].append("stop_triggered")
    if error_events:
        report["failures"].append("error_or_failure_event_recorded")
    if run["status"] == "failed":
        report["failures"].append("run_status_failed")
    if run["status"] not in {"completed", "failed", "running"}:
        report["failures"].append("run_status_invalid")
    if ci_failed:
        report["failures"].extend(f"ci_workflow_failed:{name}" for name in ci_failed)
    if ci_sha and not ci_sha_matches:
        report["failures"].append("ci_commit_sha_mismatch")

    hard_failure = bool(
        report["failures"]
        or run["status"] == "failed"
        or (observations and observation_failures)
    )
    if hard_failure:
        report["soak_status"] = "FAIL"
        report["overall_status"] = "FAIL"
    elif run["status"] == "running":
        report["soak_status"] = "INCOMPLETE"
        report["overall_status"] = "INCOMPLETE"
    elif not all(checks.values()):
        report["soak_status"] = (
            "INCOMPLETE" if run["status"] == "completed" else "FAIL"
        )
        report["overall_status"] = "INCOMPLETE" if run["status"] == "completed" else "FAIL"
    else:
        report["soak_status"] = "PASS"
        report["overall_status"] = "PASS"

    if not provider_declared:
        report["limitations"].append(
            "No production read-only provider and production inference identity are declared; "
            "a test/placeholder provider cannot establish operational soak acceptance."
        )
    if not duration_met:
        report["limitations"].append(
            "The configured continuous soak duration has not been demonstrated by the terminal event."
        )
    if not heartbeat_coverage:
        report["limitations"].append(
            "Heartbeat and shadow-observation counts do not prove continuous sampling at the configured interval."
        )
    if not commit_valid:
        report["limitations"].append(
            "The source commit SHA is missing or invalid; this run is not reproducibly tied to a revision."
        )
    if ci_evidence is None:
        report["limitations"].append(
            "Supply a CI evidence JSON file to evaluate CI, Tests, Lint, and Secrets scan conclusions."
        )
    elif not ci_green:
        report["limitations"].append(
            "The supplied CI evidence is incomplete, failed, or does not match the soak source commit."
        )
    if run["status"] == "running":
        report["limitations"].append(
            "The selected run is still active; an active run cannot pass acceptance."
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a read-only, fail-closed Paper soak acceptance report."
    )
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--run-id", help="Specific Paper soak run; defaults to latest")
    parser.add_argument("--ci-evidence", type=Path, help="JSON snapshot of required GitHub workflow conclusions")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.db_path, args.run_id, args.ci_evidence)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2, "NOT_RUN": 3}[
        report["overall_status"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
