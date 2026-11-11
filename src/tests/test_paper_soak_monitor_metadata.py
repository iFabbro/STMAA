import json
import sqlite3

import pytest

from src.utils.paper_soak_monitor import PaperSoakMonitor


@pytest.mark.parametrize(
    ("broker_access", "expected_access"),
    [("disabled", "disabled"), ("read_only", "read_only")],
)
def test_monitor_persists_accurate_broker_access_semantics(
    tmp_path, broker_access, expected_access
):
    database = tmp_path / "paper-soak.db"
    monitor = PaperSoakMonitor(
        db_path=database,
        duration_seconds=1,
        heartbeat_interval_seconds=1,
        broker_access=broker_access,
    )

    monitor.start()
    monitor.finish()

    with sqlite3.connect(database) as connection:
        run_meta = json.loads(
            connection.execute("SELECT meta FROM runs").fetchone()[0]
        )
        started_payload = json.loads(
            connection.execute(
                "SELECT payload FROM events WHERE event_type = 'session_started'"
            ).fetchone()[0]
        )

    for payload in (run_meta, started_payload):
        assert payload["broker_access"] == expected_access
        assert payload["broker_mutations_enabled"] is False
        assert payload["order_submission"] == "disabled"


def test_monitor_rejects_unknown_broker_access_semantics(tmp_path):
    with pytest.raises(ValueError, match="broker_access"):
        PaperSoakMonitor(db_path=tmp_path / "invalid.db", broker_access="enabled")
