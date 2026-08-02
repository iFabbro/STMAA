# STMAA

<p align="center">
  <img src="./Logo.png" alt="STMAA logo" width="180" />
</p>

[![Python](https://img.shields.io/badge/Python-3.13-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-42%2B%20passed-brightgreen?style=for-the-badge)]()
[![License](https://img.shields.io/badge/License-MIT-lightgrey?style=for-the-badge)]()

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
