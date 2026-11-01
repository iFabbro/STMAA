# Paper soak heartbeat monitor

The broker-independent monitor records the lifecycle of a planned Paper soak and
periodic process heartbeats in the existing SQLite event database
(`logs/stmaa.db` by default).

## Run

From the repository root, with the project environment active:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_monitor
```

The default duration is 48 hours and the default heartbeat interval is 60 seconds.
For a short local smoke test, use an explicit shorter duration and a temporary
database:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_monitor \
  --duration-hours 0.01 \
  --heartbeat-seconds 5 \
  --db-path /tmp/stmaa-paper-soak-monitor.db
```

The monitor prints its run ID and terminal status when it exits. Events are
persisted in the SQLite `runs` and `events` tables and can be inspected using
the existing SQLite tooling.

## Scope and safety boundary

This component only writes monitor lifecycle and heartbeat events to SQLite.
It does not import or initialize an Alpaca client, read broker credentials,
query account/order/position endpoints, or submit, replace, cancel, or close
orders. It does not start a trading process.

A completed heartbeat session is **not** evidence that broker connectivity,
market-data freshness, exposure, risk, or persisted broker state remained
healthy. Broker polling, automatic stop thresholds, restart recovery, and final
soak acceptance criteria are separate implementation steps. Until those steps
are implemented and reviewed, this component alone must not be treated as a
48-hour soak controller or as authorization to start the soak.

An interrupted or failed process records a failed run when it can still write
to SQLite. A hard process or host failure may leave a run in `running` state;
recovery and interpretation of such runs are addressed separately.
