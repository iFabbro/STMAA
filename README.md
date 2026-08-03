<p align="center">
  <img src="./Logo.png" alt="Logo STMAA" width="180" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13-blue?logo=python&style=for-the-badge" alt="Python 3.13" />
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

<<<<<<< HEAD
STMAA is a modular research and execution framework for algorithmic trading. It unifies backtesting, paper trading, and live execution behind a weight-based architecture with built-in risk controls and monitoring.

## Demo

<p align="center">
  <img src="./hub-screenshot.png" alt="STMAA hub screenshot" />
</p>

=======
STMAA is not another trading bot. It is a modular research and execution framework built around a weight-centric architecture, allowing the same strategy contract to power backtesting, paper trading, and live execution.
>>>>>>> a6a0c59 (Finalize README structure)

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

## Quick Start

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
├── utils/
└── tests/
```

| Component | Role |
|---|---|
| `trade.py` | Operational entry point for the trading workflow. |
| `hub.py` | Main orchestration layer. |
| `src/execution/broker_executor.py` | Broker access, positions, open orders, and execution constraints. |
| `src/utils/sqlite_logger.py` | Structured logging to SQLite. |
| `src/tests/` | Automated tests for the core modules. |
| `scripts/` | Local helper scripts and validation tools. |

## Roadmap

- ✅ MVP.
- ⬜ Real market data integration.
- ⬜ DRL policy.
- ⬜ Portfolio optimization.
- ⬜ CI/CD pipeline.
- ⬜ Live trading.

## Why STMAA vs. a Monolithic Bot?

| Feature | STMAA | Typical monolithic bot |
|---|:---:|:---:|
| Weight-based execution | ✅ | ❌ |
| Modular architecture | ✅ | ⚠️ |
| Research oriented | ✅ | ⚠️ |
| Integrated backtesting | ✅ | ✅ |
| Risk overlay | ✅ | ⚠️ |
| Monitoring hub | ✅ | ❌ |

## Repository Status

The test suite is green and covers the metrics engine, report generator, PNG fallback, and CLI.
