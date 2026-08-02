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

## Overview

STMAA now includes advanced performance metrics, JSON and CSV exports, markdown reporting, threshold alerts, PNG summaries, and a CLI to generate everything in one command.

## Workflow

- Compute Sharpe annualized, Sortino annualized, max drawdown, drawdown duration, and total return.
- Serialize metrics to JSON and append them to a historical CSV.
- Generate a markdown report with trend deltas and alert thresholds from YAML.
- Export a PNG summary when Kaleido is available.
- Run the whole reporting pipeline from a single CLI command.

## Repository status

The test suite is green and covers the metrics engine, report generator, PNG fallback, and CLI.
