from src.utils.sqlite_logger import start_run, log_event, write_snapshot, latest_snapshot, recent_events, end_run

run_id = start_run(mode="test", meta={"suite": "phase4-min"})
log_event(run_id, "test", "startup", "sqlite logger ok", payload={"x": 1})
write_snapshot(
    run_id,
    "test",
    portfolio_value=1000.0,
    cash=500.0,
    equity=1000.0,
    drawdown=0.01,
    exposure=0.3,
    regime="neutral",
    positions_json={"AAPL": 1},
    payload={"ok": True},
)
print({"run_id": run_id, "snapshot": latest_snapshot(), "events": recent_events(2)})
end_run(run_id, status="completed")
