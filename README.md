# STMAA — Structural Trading Multi-Agent Architecture

STMAA is a modular multi-agent trading system built around a weight-centric contract between strategy and execution. The project includes data ingestion, feature engineering, risk controls, backtesting, paper trading, and a terminal-based monitoring hub.

## Highlights

- Weight-centric strategy interface for execution consistency.
- Execution-consistent backtesting with costs and slippage.
- Alpaca paper trading integration.
- Risk overlay with position caps, volatility scaling, and circuit breakers.
- Terminal hub with live state from `logs/hub_state.json`.
- Ready path for real market data, DRL policies, and structured logging.

## Project status

The current codebase is an operational MVP:
- Python 3.12 virtual environment configured.
- Paper trading bot running.
- Terminal hub running without Streamlit.
- Main test scripts already pass.
- README roadmap and project docs are in place.

## Installation

### 1. Create and activate the virtual environment

```bash
python3.12 -m venv venv
source venv/bin/activate
```

### 2. Upgrade pip

```bash
pip install --upgrade pip
```

### 3. Install dependencies

```bash
pip install numpy pandas requests python-dotenv pytest black flake8
pip install yfinance alpaca-py scipy scikit-learn gymnasium stable-baselines3
```

If you later want a full DRL stack with PyTorch:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 4. Configure Alpaca keys

Copy the example environment file and edit it:

```bash
cp .env.example .env
nano .env
```

Example values:

```bash
ALPACA_API_KEY=your_api_key_here
ALPACA_API_SECRET=your_api_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

## Usage

### Run the training / backtest demo

```bash
source venv/bin/activate
python train.py
```

### Run paper trading

```bash
source venv/bin/activate
python trade.py
```

### Run the terminal hub

```bash
source venv/bin/activate
python hub.py
```

### Run tests

```bash
python src/tests/test_data_processor.py
python src/tests/test_weight_interface.py
python src/tests/test_trading_env.py
python src/tests/test_backtest_engine.py
python src/tests/test_broker_executor.py
python src/tests/test_risk_overlay.py
```

Or run all tests with pytest:

```bash
python -m pytest src/tests -v
```

## Architecture

### Data layer

The data layer ingests market data, cleans it, and adds technical indicators.

Features currently implemented:
- RSI
- MACD
- Bollinger Bands
- ATR

File:
- `src/data/data_processor.py`

### Strategy layer

Strategies produce a normalized portfolio weight vector. This is the core contract shared by backtest and execution.

Files:
- `src/strategy/weight_interface.py`
- `src/strategy/risk_overlay.py`

### Agent layer

The environment wraps market data as an MDP for DRL agents.

File:
- `src/agents/trading_env.py`

### Backtest layer

The backtest engine simulates execution with transaction cost and slippage, then computes standard performance metrics.

File:
- `src/backtest/backtest_engine.py`

### Execution layer

The broker executor currently supports Alpaca paper trading and persists execution state.

File:
- `src/execution/broker_executor.py`

### Monitoring layer

The hub reads structured state from `logs/hub_state.json` and displays:
- portfolio value,
- cash,
- equity,
- equity curve ASCII sparkline,
- recent orders,
- runtime status.

File:
- `hub.py`

## Key files

- `src/config.py`
- `src/data/data_processor.py`
- `src/strategy/weight_interface.py`
- `src/strategy/risk_overlay.py`
- `src/agents/trading_env.py`
- `src/backtest/backtest_engine.py`
- `src/execution/broker_executor.py`
- `train.py`
- `trade.py`
- `hub.py`
- `ROADMAP.md`

## Data flow

1. Market data enters the system.
2. Data is cleaned and enriched with indicators.
3. A strategy produces a portfolio weight vector.
4. The backtest engine simulates execution.
5. The broker executor translates weights into orders.
6. Risk overlay constrains exposure.
7. The hub reads the latest state and shows it in the terminal.

## Current runtime configuration

Current operational defaults:
- Tickers: `AAPL`, `GOOGL`, `MSFT`, `AMZN`, `TSLA`, `NVDA`, `META`
- Interval: 300 seconds
- Mode: Alpaca paper trading
- Terminal hub refresh: 30 seconds

## Real market data

The next upgrade is to replace the current mock data generation in `trade.py` with a real market data service using Alpaca Market Data API. Alpaca’s official Python SDK supports real-time and historical market data access. [web:89][web:90][web:95]

Recommended approach:
- create a `MarketDataService`,
- fetch bars from Alpaca,
- cache the latest snapshots,
- feed the same schema into the strategy and hub.

## DRL policy

The next upgrade is to replace random weights with a real trained agent. The clean path is:
- train PPO/SAC against `TradingEnv`,
- save the model,
- load it inside `trade.py`,
- emit portfolio weights from the policy instead of random draws.

Recommended files:
- `src/agents/drl_agent.py`
- `models/drl/`

## Advanced metrics

Recommended next metrics:
- max drawdown,
- rolling drawdown,
- gross and net exposure,
- volatility regime,
- market regime classification,
- win rate and turnover,
- trade-level PnL.

These can be added to the backtest engine and to the hub state. Logging metrics in structured form is a standard pattern for trading monitoring systems. [web:106][web:107][web:110]

## Structured logging

The current JSON snapshot is enough for the MVP, but the next step should be structured logging:
- JSON Lines for event streams, or
- SQLite for queryable historical state.

That makes it easier to build richer dashboards and post-trade analysis. [web:106][web:108][web:110]

## Troubleshooting

### `python: command not found`

Activate the virtual environment:

```bash
source venv/bin/activate
```

### Alpaca keys not found

Check `.env` values and ensure the file is in the project root.

### `No matching distribution found for torch`

Use Python 3.12 and install PyTorch with the CPU wheel index if needed.

### Hub shows no live data

Check that:
- `trade.py` is running,
- `logs/hub_state.json` exists,
- the bot is writing state on each iteration.

### API/data issues

If Alpaca market data requests fail, confirm the SDK and credentials. Alpaca’s market data API supports historical and real-time data via Python SDK and HTTP/WebSocket endpoints. [web:89][web:90][web:95]

## Roadmap

### Completed
- Data processor
- Weight-centric strategy interface
- Trading environment
- Backtest engine
- Alpaca broker executor
- Risk overlay
- Paper trading bot
- Terminal hub
- Project documentation

### Next
1. Replace mock data with real market data.
2. Train a real DRL policy.
3. Add drawdown, exposure, and regime metrics.
4. Move from JSON snapshot to structured logs or SQLite.
5. Add persistent historical reporting.

## Notes

This project is designed as a practical research and execution framework rather than a fully production-hardened trading stack. The architecture is intentionally modular so each layer can be replaced or improved independently.

## License

Add your preferred license here.

## Author

STMAA project by Marco Fabroni.