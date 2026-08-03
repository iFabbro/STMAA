<p align="center">
  <img src="./Logo.png" alt="Logo STMAA" width="180" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13-blue?logo=python&style=for-the-badge" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/Tests-Passing-brightgreen?style=for-the-badge" alt="Tests Passing" />
  <img src="https://img.shields.io/badge/Status-MVP-orange?style=for-the-badge" alt="Status MVP" />
  <img src="https://img.shields.io/badge/Architecture-Multi--Agent-purple?style=for-the-badge" alt="Architecture Multi-Agent" />
  <img src="https://img.shields.io/badge/Execution-Alpaca-blue?style=for-the-badge" alt="Execution Alpaca" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License" />
</p>

# STMAA
Structural Trading Multi-Agent Architecture

STMAA is a modular research and execution framework for algorithmic trading. Instead of coupling strategy logic, execution, broker communication, and monitoring into a single trading bot, STMAA separates each responsibility into independent components connected through a weight-centric portfolio interface.

The same strategy contract can be used consistently for:

- research
- backtesting
- paper trading
- live execution

## Demo

<p align="center">
  <img src="./hub-screenshot.png" alt="STMAA hub screenshot" />
</p>

## Why STMAA?

Most trading bots bundle signal generation, execution, and broker logic into a single workflow.

STMAA separates these responsibilities into a layered architecture where:

- market data feeds the system,
- feature engineering prepares inputs,
- strategies generate portfolio weights,
- the risk overlay constrains exposure,
- the broker executor turns weights into orders,
- the broker handles execution,
- logging records system activity,
- the monitoring hub keeps the workflow visible.

This separation makes the framework easier to extend, debug, test, and evolve over time.

## Features

- Modular multi-agent architecture.
- Weight-centric execution model.
- Execution-consistent backtesting.
- Alpaca paper trading integration.
- Risk overlay.
- SQLite structured logging.
- Terminal monitoring hub.
- Automated test suite.
- DRL-ready architecture.
- Research-first design.

## Architecture

```mermaid
flowchart LR
  MD[Market Data] --> FE[Feature Engineering]
  FE --> ST[Strategy]
  ST --> PW[Portfolio Weights]
  PW --> RO[Risk Overlay]
  RO --> BE[Broker Executor]
  BE --> BR[Broker]
  BE --> LG[SQLite Logger]
  BE --> HB[Terminal Hub]
```

## Why STMAA instead of a monolithic trading bot?

| Feature | STMAA | Typical trading bot |
|---|---:|---:|
| Modular architecture | ✅ | ⚠️ |
| Weight-based execution | ✅ | ❌ |
| Risk overlay | ✅ | ⚠️ |
| Execution-consistent backtesting | ✅ | ⚠️ |
| Paper trading | ✅ | ✅ |
| Live execution ready | ✅ | ⚠️ |
| Monitoring hub | ✅ | ❌ |
| Research oriented | ✅ | ⚠️ |

## Quick Start

### Requirements

- Python 3.10 or newer.
- A virtual environment is recommended.
- Alpaca paper trading credentials if you want to test the execution path.

### Setup

```bash
git clone https://github.com/iFabbro/STMAA.git
cd STMAA
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run the main workflow

```bash
PYTHONPATH=. python3 trade.py
```

### Run the orchestration hub

```bash
PYTHONPATH=. python3 hub.py
```

### Run the test suite

```bash
PYTHONPATH=. python3 -m pytest src/tests -q
```

## Example Session

```text
Loading market data...
Computing indicators...
Generating portfolio weights...
Applying risk overlay...
Submitting paper orders...
Updating hub state...
Cycle completed successfully.
```

## Project Structure

```text
src/
├── agents/
├── backtest/
├── data/
├── execution/
├── strategy/
├── tests/
└── utils/

trade.py
hub.py
ROADMAP.md
```

| Component | Role |
|---|---|
| `trade.py` | Operational entry point for the trading workflow. |
| `hub.py` | Main orchestration layer. |
| `src/execution/broker_executor.py` | Broker access, positions, open orders, and execution constraints. |
| `src/utils/sqlite_logger.py` | Structured logging to SQLite. |
| `src/tests/` | Automated tests for the core modules. |
| `scripts/` | Local helper scripts and validation tools. |

## Current Status

Current development stage:

- Modular architecture.
- Weight interface.
- Risk overlay.
- Backtesting engine.
- Paper trading.
- Terminal hub.
- Structured logging.
- Automated tests.

Next milestones:

- Real market data.
- DRL policy integration.
- Portfolio optimisation.
- CI/CD pipeline.
- Live trading.
- Historical performance dashboard.

## Contributing

Contributions are welcome.

Please:

- Fork the repository.
- Create a feature branch.
- Run the test suite.
- Open a pull request.

## License

Released under the MIT License.
See the `LICENSE` file for details.
