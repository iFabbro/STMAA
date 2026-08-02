<p align="center">
  <img src="./Logo.png" alt="Logo STMAA" width="180" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&style=for-the-badge" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/Tests-Passing-brightgreen?style=for-the-badge" alt="Tests Passing" />
  <img src="https://img.shields.io/badge/Status-MVP-orange?style=for-the-badge" alt="Status MVP" />
  <img src="https://img.shields.io/badge/Architecture-Multi--Agent-purple?style=for-the-badge" alt="Architecture Multi-Agent" />
  <img src="https://img.shields.io/badge/Execution-Alpaca-blue?style=for-the-badge" alt="Execution Alpaca" />
  <img src="https://img.shields.io/badge/Backtest-Execution%20Consistent-success?style=for-the-badge" alt="Backtest Execution Consistent" />
  <img src="https://img.shields.io/badge/DRL-Ready-blueviolet?style=for-the-badge" alt="DRL Ready" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License" />
</p>

# STMAA

Structural Trading Multi-Agent Architecture

STMAA is a modular research and execution framework for algorithmic trading. It unifies backtesting, paper trading, and live execution behind a weight-based architecture with built-in risk controls and monitoring.

<p align="center">
  <em>Insert a terminal screenshot or short demo GIF of the hub here.</em>
</p>

## Demo

<p align="center">
  <img src="./hub-screenshot.png" alt="STMAA hub screenshot" />
</p>


## Why STMAA?

Most trading bots couple signal generation, execution, and broker logic in a single workflow.

STMAA separates these responsibilities through a weight-centric architecture where:

- strategies generate portfolio weights,
- risk overlays constrain exposure,
- execution translates weights into broker orders,
- monitoring remains independent from trading logic.

This separation makes research, testing, and deployment easier to manage.

## Overview

STMAA is designed as a modular research and execution framework for algorithmic trading. It keeps strategy logic, portfolio weighting, risk checks, execution, and monitoring separated so each part can evolve independently.

The current focus is a robust paper-trading workflow with broker safeguards, structured logging, and a growing test suite.


The project is designed to keep strategy logic, portfolio weighting, risk checks, and execution concerns separated. That makes it easier to test, debug, and evolve the system without coupling everything into a single trading script.

STMAA currently focuses on a robust paper-trading workflow, broker execution safeguards, structured logging, and a growing test suite.

## Features

- Multi-agent trading architecture.
- Weight-based execution interface.
- Backtesting and paper-trading workflow.
- Broker execution with guardrails.
- SQLite logging for local observability.
- Risk controls for exposure and drawdown.
- Automated test coverage for core modules.
- CLI support for report generation and validation.

## Architecture

```mermaid
flowchart LR
    MD[Market Data] --> FE[Feature Engineering]
    FE --> ST[Strategy]
    ST --> PW[Portfolio Weights]
    PW --> RO[Risk Overlay]
    RO --> BE[Broker Executor]
    BE --> BR[Broker]
    BE --> LG[Logger]
    BE --> HB[Hub]
```

## Quick Start

|
| Weight-based execution | ✅ | ❌ |
| Modular architecture | ✅ | ⚠️ |
| Research oriented | ✅ | ⚠️ |
| Integrated backtesting | ✅ | ✅ |
| Risk overlay | ✅ | ⚠️ |
| Monitoring hub | ✅ | ❌ |


### Requirements

- Python 3.10 or newer.
- A virtual environment is recommended.
- Broker credentials for paper trading if you want to test the execution path.

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

## Usage

The main entry point is `trade.py`, which executes the trading workflow end to end. The orchestration layer is in `hub.py`, which coordinates the broader system behavior.

Core implementation files include:
- `src/execution/broker_executor.py` for broker access and execution rules.
- `src/utils/sqlite_logger.py` for structured local logging.
- `src/tests/` for automated checks around the most important modules.

## Project Structure

| Component | Role |
|---|---|
| `trade.py` | Operational entry point for the trading workflow. |
| `hub.py` | Main orchestration layer. |
| `src/execution/broker_executor.py` | Broker access, positions, open orders, and execution constraints. |
| `src/utils/sqlite_logger.py` | Structured logging to SQLite. |
| `src/tests/` | Automated tests for the core modules. |
| `scripts/` | Local helper scripts and validation tools. |

## Roadmap

- Improve reconciliation of capital already reserved by accepted orders.
- Expand observability for the execution pipeline.
- Harden broker state and order lifecycle handling.
- Add richer reporting and analysis tools.
- Introduce CI-backed test badges and build verification.
- Add more examples for paper trading and live trading workflows.

## Contributing

Contributions are welcome. Please keep changes focused, run the test suite before opening a pull request, and open a separate `CONTRIBUTING.md` file if you want to document a more detailed workflow.

## License

STMAA is released under the MIT License. See the `LICENSE` file for details.
