# Paper soak heartbeat monitor

The Paper soak monitor records a planned session lifecycle, periodic
heartbeats, and shadow observations in the existing SQLite event database
(`logs/stmaa.db` by default).

## Components

- `PaperSoakMonitor`: session lifecycle and heartbeat persistence.
- `PaperSoakStopController`: fail-closed evaluation of broker, exposure,
  market-data, persistence, and heartbeat health observations.
- `PaperSoakShadowRunner`: invokes an injected observation provider, records
  hypothetical strategy decisions, and connects each health observation to
  the stop controller.
- `paper_soak_report`: read-only report generator for persisted run evidence
  and an operator-supplied snapshot of GitHub workflow conclusions.

## Run the broker-independent heartbeat monitor

From the repository root, with the project environment active:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_monitor \
  --branch main \
  --commit-sha <40-character-commit-sha>
```

The default duration is 48 hours and the default heartbeat interval is 60
seconds. For a short local smoke test, use an explicit shorter duration and a
temporary database:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_monitor \
  --duration-hours 0.01 \
  --heartbeat-seconds 5 \
  --branch main \
  --commit-sha <40-character-commit-sha> \
  --db-path /tmp/stmaa-paper-soak-monitor.db
```

This CLI command runs only the broker-independent heartbeat monitor. It does
not produce shadow observations and therefore cannot by itself pass the
operational acceptance report.

## Shadow runner integration contract

The shadow runner is a library component. A concrete production provider is
available in `src.utils.paper_soak_production_provider`; it reuses
`PaperTradingBot.get_market_data()`, `PaperTradingBot.generate_weights()`
and `PPOAgent.predict_weights()`, with the same verified account and positions
snapshot passed into inference. It then reads open orders, estimates pending
order exposure, validates VIX and applies the production risk overlay to produce
a hypothetical risk-adjusted decision. It does not invoke the trading loop or
any order-execution method.

The generic runner still accepts a zero-argument observation provider returning
`PaperSoakShadowObservation`, containing:

- a complete `PaperSoakHealthSnapshot`;
- a JSON-serializable `decision` payload representing a hypothetical strategy
  decision (for example, target weights and a decision label).

The provider is an explicit trust boundary. Any future concrete provider must
use read-only market/account/order/position observations and strategy inference
only. It must not call `execute_trade`, broker order submission, replacement,
cancellation, liquidation, or any other broker mutation. The runner itself
does not import, instantiate, or receive a broker executor/client, and has no
credentials or network access.

Example wiring with an application-owned read-only provider:

```python
from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils.paper_soak_shadow_runner import (
    PaperSoakShadowObservation,
    PaperSoakShadowRunner,
)

def read_only_observation_provider() -> PaperSoakShadowObservation:
    # Implement using approved read-only data and inference dependencies.
    # Return a complete health snapshot and a hypothetical decision.
    raise NotImplementedError

monitor = PaperSoakMonitor(
    branch="main",
    commit_sha="<40-character-commit-sha>",
    run_metadata={
        "provider_name": "name of the concrete provider",
        "provider_kind": "production_read_only",
        "production_strategy_inference": True,
    },
)
runner = PaperSoakShadowRunner(
    monitor,
    observation_provider=read_only_observation_provider,
)
run_id = runner.run()
```

The provider metadata is an explicit declaration by the caller, not proof of
the provider's implementation. The production provider must be independently
reviewed and tested. Keep the monitor heartbeat interval at least as frequent
as the runner observation interval so that the report can verify expected
sampling coverage.

The runner records each observation and hypothetical decision, evaluates the
stop controller, emits a heartbeat, and terminates the session as failed when
a stop threshold is reached. Missing or malformed observations fail closed.
The stop is sticky and cannot be reset within the same monitor session.

## Restart and reconnection semantics

A process restart does not resume an interrupted soak run. SQLite records the
owning process identity; when a later process proves that owner is dead,
`recover_orphaned_runs` finalizes the orphaned run as `failed`. Late heartbeats
or observations against that run are rejected. A new process may create a new
run, but the interrupted run remains failed evidence and must not be counted
toward a continuous soak duration.

A read-only provider connection error is terminal for the current shadow
run. The runner records the failure and stops; it does not silently retry,
reuse a stale observation, or mark the run healthy. Any future reconnect policy
must first obtain a complete fresh broker snapshot, revalidate exposure and
market-data freshness, and remain read-only. Until such a policy is explicitly
implemented and tested, restarting the shadow runner is a new run, not a
continuation of the previous one.

The stop controller restores its latched stop from the SQLite event log when
reconstructed against the same active run. A latched stop remains authoritative
even if subsequent health observations are healthy. A confirmed-dead process
causes the owning run to be finalized as failed rather than re-opened.

## Production read-only provider

After configuring a valid Alpaca Paper account and installing a compatible PPO
artifact plus its metadata, run the production provider from the repository
root:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_production_provider \
  --branch main \
  --commit-sha <40-character-commit-sha> \
  --duration-hours 48 \
  --interval-seconds 60 \
  --db-path logs/stmaa.db
```

The command validates the startup configuration, uses the production market
data contract and PPO inference path, reads the Paper account, positions and
open orders, reconciles corporate-action activity, estimates gross exposure
including pending orders, validates the latest completed-session VIX close,
and applies the existing risk overlay to a hypothetical target. A missing or
incompatible model, stale/invalid data, unreadable broker state, unknown
pending-order notional, inconsistent account identity, persistence failure or
exposure above the configured gross limit fails closed.

The provider uses broker GET/read operations only. Account refresh and
corporate-action reconciliation can update STMAA's local persisted execution
metadata; they do not submit, replace, cancel or close broker orders. The
hypothetical decision is recorded in SQLite. It does not call
`PaperTradingBot.execute_trade()` or `BrokerExecutor.execute_weights()`.

Use a short duration and a temporary database for a local smoke test before
considering an operational run. This command does not start automatically, and
the 48-hour soak remains deferred until the provider, model artifact, Paper
account, and operational preflight have been independently reviewed. Do not
interpret a successful short smoke test as a completed soak.

## Automated acceptance report

Generate a JSON report for the latest run:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_report \
  --db-path logs/stmaa.db \
  --output reports/paper-soak-report.json
```

Select a specific run with `--run-id <uuid>`. The command reads the SQLite
database in read-only mode and does not contact Alpaca, submit orders, or call
GitHub. Exit codes are: `0=PASS`, `1=FAIL`, `2=INCOMPLETE`, `3=NOT_RUN`.

The report checks terminal run state, configured continuous elapsed duration,
heartbeat and observation coverage at the configured interval, every shadow
observation, mandatory health signals, stop and error events, disabled order
submission, provider declaration, and source commit SHA. It requires at least
`ceil(duration_seconds / heartbeat_interval_seconds)` heartbeat events and
shadow observations, with the terminal heartbeat count matching the persisted
heartbeat events. Missing mandatory health telemetry or a failed safety
control is a failure. An active, short, under-sampled, unobserved, or
insufficiently identified run is incomplete. No matching run is `NOT_RUN`.

### CI evidence input

GitHub workflow status is deliberately not inferred from the local SQLite
database. Supply a JSON snapshot for the same source commit:

```json
{
  "commit_sha": "<40-character commit SHA>",
  "workflows": {
    "CI": "success",
    "Tests": "success",
    "Lint": "success",
    "Secrets scan": "success"
  }
}
```

Then generate the report with:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_report \
  --db-path logs/stmaa.db \
  --run-id <run-uuid> \
  --ci-evidence reports/ci-evidence.json \
  --output reports/paper-soak-report.json
```

All four workflow conclusions must be `success` and their evidence commit SHA
must exactly match the soak run's recorded 40-character source commit SHA.
Missing CI evidence or incomplete workflow results prevent a `PASS`; a failed
workflow or commit mismatch produces `FAIL`. The supplied CI JSON is an
operator-provided snapshot, not a cryptographically verified GitHub attestation.
Verify the workflow conclusions directly on GitHub before relying on the report.

A `PASS` requires all checks to pass, including a completed continuous
48-hour run with adequate heartbeat and shadow-observation coverage, a declared
production read-only provider, production strategy inference, a recorded source
commit, and green CI evidence for that same commit. The provider declaration
is metadata and does not prove the provider's implementation is safe; code
review and independent integration tests remain necessary. A test or
placeholder provider cannot establish operational acceptance. The report is
an evidence summary, not a certification or authorization to trade live.

## Safety boundary and current limitations

The generic runner does not itself poll Alpaca or obtain market data, account
equity, positions, open orders, persisted broker state, or VIX; those operations
are supplied by the concrete production provider described above. A completed
run using a test or placeholder provider is **not** evidence that the
production strategy, broker state, or market-data pipeline was healthy. The
production provider is read-only and intentionally terminates on a provider
error; it does not implement an automatic reconnect policy. Operational
preflight and independent review remain outstanding.

No component described here submits, replaces, cancels, or closes broker
orders. The 48-hour soak has not been started, and this implementation is not
authorization to start it.
