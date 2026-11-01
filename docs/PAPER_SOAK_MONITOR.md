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

## Run the broker-independent heartbeat monitor

From the repository root, with the project environment active:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_monitor
```

The default duration is 48 hours and the default heartbeat interval is 60
seconds. For a short local smoke test, use an explicit shorter duration and a
temporary database:

```bash
PYTHONPATH=. python -m src.utils.paper_soak_monitor \
  --duration-hours 0.01 \
  --heartbeat-seconds 5 \
  --db-path /tmp/stmaa-paper-soak-monitor.db
```

## Shadow runner integration contract

The shadow runner is a library component; it is not yet wired to the production
`PaperTradingBot` or its PPO inference path. The caller supplies a zero-argument
observation provider returning `PaperSoakShadowObservation`, containing:

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

monitor = PaperSoakMonitor()
runner = PaperSoakShadowRunner(
    monitor,
    observation_provider=read_only_observation_provider,
)
run_id = runner.run()
```

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

## Safety boundary and current limitations

The runner does not itself poll Alpaca or obtain market data, account equity,
positions, open orders, persisted broker state, or VIX. A completed run using
a test or placeholder provider is **not** evidence that the production
strategy, broker state, or market-data pipeline was healthy. In particular,
the production `PaperTradingBot`/PPO path has not yet been connected to this
runner. A concrete read-only provider, reconnect policy, final soak acceptance
report, and operational preflight remain outstanding.

No component described here submits, replaces, cancels, or closes broker
orders. The 48-hour soak has not been started, and this implementation is not
authorization to start it.
