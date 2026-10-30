#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

import json
import math
import os
import time

from dotenv import load_dotenv

load_dotenv()

from src.utils.sqlite_logger import start_run, log_event, write_snapshot

RUN_ID = start_run(mode="hub", meta={"component": "hub"})
HUB_STATE = Path("logs/hub_state.json")


class Colors:
    HEADER = "\033[94m"
    TITLE = "\033[36m"
    POS = "\033[92m"
    NEG = "\033[91m"
    ACCENT = "\033[93m"
    MUTED = "\033[90m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


SPARKS = "▁▂▃▄▅▆▇█"
NA = "n/a (not persisted)"


def clear_screen():
    os.system("clear" if os.name == "posix" else "cls")


def load_state():
    if HUB_STATE.exists():
        try:
            state = json.loads(HUB_STATE.read_text())
            return state if isinstance(state, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
    return {}


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_curve(value):
    if not isinstance(value, list):
        return []
    if any(isinstance(item, bool) for item in value):
        return []
    numbers = [_finite_number(item) for item in value]
    if any(item is None for item in numbers):
        return []
    return numbers


def sparkline(series, width=48):
    if not series:
        return ""
    vals = series[-width:]
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return SPARKS[0] * len(vals)
    return "".join(
        SPARKS[int((x - mn) / (mx - mn) * (len(SPARKS) - 1))]
        for x in vals
    )


def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text:^72}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 72}{Colors.ENDC}\n")


def print_metric(label, value, change=None):
    change_str = ""
    if change is not None:
        color = Colors.POS if change > 0 else Colors.NEG
        symbol = "↑" if change > 0 else "↓"
        change_str = f" {color}{symbol}{change:+.2f}%{Colors.ENDC}"
    print(
        f"  {Colors.TITLE}{label:<26}{Colors.ENDC} "
        f"{Colors.BOLD}{value}{Colors.ENDC}{change_str}"
    )


def get_live_quotes(tickers):
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    if not api_key or not api_secret:
        return {ticker: {"price": 0.0, "change_pct": 0.0} for ticker in tickers}
    try:
        from src.data.alpaca_feed import AlpacaFeed

        feed = AlpacaFeed(api_key=api_key, api_secret=api_secret)
        return feed.get_latest_quotes(tickers)
    except Exception as exc:
        print(f"{Colors.MUTED}  [hub] prezzi live non disponibili: {exc}{Colors.ENDC}")
        return {ticker: {"price": 0.0, "change_pct": 0.0} for ticker in tickers}


log_event(RUN_ID, "hub", "startup", "hub.py started")


def render():
    state = load_state()
    tickers = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META"]

    last_ts = state.get("timestamp")
    orders = state.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    portfolio_value = _finite_number(state.get("portfolio_value"))
    cash = _finite_number(state.get("cash"))
    equity = _finite_number(state.get("equity"))
    equity_curve = _valid_curve(state.get("equity_curve"))
    metrics = state.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    def metric_value(name, formatter):
        value = _finite_number(metrics.get(name))
        return formatter(value) if value is not None else NA

    max_drawdown = _finite_number(metrics.get("max_drawdown"))
    sharpe = _finite_number(metrics.get("sharpe_ratio"))
    sortino = _finite_number(metrics.get("sortino_ratio"))
    calmar = _finite_number(metrics.get("calmar_ratio"))
    exposure = _finite_number(metrics.get("exposure"))
    regime = metrics.get("regime")
    total_return = _finite_number(metrics.get("total_return"))
    annualized_return = _finite_number(metrics.get("annualized_return"))

    quotes = get_live_quotes(tickers)
    if not isinstance(quotes, dict):
        quotes = {}

    clear_screen()
    print_header("STMAA TRADING HUB")

    account_fresh = state.get("account_data_fresh") is True
    system_status = "Account snapshot fresh" if account_fresh else "Account snapshot unavailable or stale"
    mode = state.get("mode") or "unknown (not persisted)"
    print(f"{Colors.TITLE}System:{Colors.ENDC} {system_status}")
    print(f"{Colors.TITLE}Mode:{Colors.ENDC} {mode}")
    print(f"{Colors.TITLE}Last update:{Colors.ENDC} {last_ts or 'n/a (no persisted timestamp)'}")
    print(f"{Colors.TITLE}Account snapshot:{Colors.ENDC} {state.get('account_snapshot_at') or 'n/a'}")
    print(f"{Colors.TITLE}Account data source:{Colors.ENDC} {state.get('account_data_source') or 'n/a'}")
    print(f"{Colors.TITLE}Orders in last cycle:{Colors.ENDC} {len(orders)}")

    if equity is not None and len(equity_curve) >= 2:
        prev_equity = equity_curve[-2]
        daily_pnl = equity - prev_equity
        daily_pct = daily_pnl / prev_equity * 100 if prev_equity else 0.0
        daily_pnl_text = f"${daily_pnl:+,.2f}"
        print_header("PORTFOLIO METRICS")
        print_metric("Portfolio Value", f"${portfolio_value:,.2f}" if portfolio_value is not None else NA)
        print_metric("Cash Available", f"${cash:,.2f}" if cash is not None else NA)
        print_metric("Total Equity", f"${equity:,.2f}")
        print_metric("Daily P&L", daily_pnl_text, daily_pct)
    else:
        print_header("PORTFOLIO METRICS")
        print_metric("Portfolio Value", f"${portfolio_value:,.2f}" if portfolio_value is not None else NA)
        print_metric("Cash Available", f"${cash:,.2f}" if cash is not None else NA)
        print_metric("Total Equity", f"${equity:,.2f}" if equity is not None else NA)
        print_metric("Daily P&L", "n/a (insufficient persisted history)")

    print_metric("Total Return", f"{total_return:+.2%}" if total_return is not None else NA)
    print_metric("Annualized Return", f"{annualized_return:+.2%}" if annualized_return is not None else NA)

    print_header("RISK METRICS")
    print_metric("Max Drawdown", f"{max_drawdown:.2%}" if max_drawdown is not None else NA)
    print_metric("Sharpe Ratio", f"{sharpe:.2f}" if sharpe is not None else NA)
    print_metric("Sortino Ratio", f"{sortino:.2f}" if sortino is not None else NA)
    print_metric("Calmar Ratio", f"{calmar:.2f}" if calmar is not None else NA)
    print_metric("Exposure", f"{exposure:.2%}" if exposure is not None else NA)
    print_metric("Regime", str(regime).upper() if regime else NA)

    write_snapshot(
        RUN_ID,
        "hub",
        payload={
            "tickers": tickers,
            "orders": len(orders),
            "equity": equity,
            "account_data_fresh": account_fresh,
            "account_data_source": state.get("account_data_source"),
        },
    )

    print_header("EQUITY CURVE")
    if equity_curve:
        print(f"{Colors.ACCENT}{sparkline(equity_curve, 48)}{Colors.ENDC}")
        print(
            f"{Colors.MUTED}min={min(equity_curve):,.2f}  "
            f"max={max(equity_curve):,.2f}  last={equity_curve[-1]:,.2f}{Colors.ENDC}"
        )
    else:
        print("n/a (no valid persisted equity history; synthetic values are not generated)")

    print_header("MONITORED TICKERS")
    print(f"\n{Colors.BOLD}{'Ticker':<10} {'Price':>10} {'Change':>10} {'Source':>12}{Colors.ENDC}")
    print(f"{Colors.UNDERLINE}{'-' * 10} {'-' * 10} {'-' * 10} {'-' * 12}{Colors.ENDC}")
    quote_sources = []
    for ticker in tickers:
        quote = quotes.get(ticker, {})
        if not isinstance(quote, dict):
            quote = {}
        price = _finite_number(quote.get("price"))
        change = _finite_number(quote.get("change_pct"))
        if price is not None and price > 0:
            quote_sources.append(True)
            price_text = f"${price:,.2f}"
            change_text = f"{change:+.2f}%" if change is not None else "n/a"
            source = "live"
            color = Colors.POS if change is not None and change >= 0 else Colors.NEG
        else:
            price_text = "n/a"
            change_text = "n/a"
            source = "unavailable"
            color = Colors.MUTED
        print(f"  {ticker:<10} {price_text:>10} {color}{change_text:>10}{Colors.ENDC} {source:>12}")

    print_header("LAST EXECUTIONS")
    print(f"\n{Colors.BOLD}{'Time':<8} {'Tk':<6} {'Side':<6} {'Qty':>7} {'Px':>9}{Colors.ENDC}")
    print(f"{Colors.UNDERLINE}{'-' * 8} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 9}{Colors.ENDC}")
    if orders:
        for order in orders[-5:][::-1]:
            if not isinstance(order, dict):
                continue
            ts = str(order.get("timestamp") or "n/a")[:16].replace("T", " ")
            ticker = order.get("ticker", "N/A")
            side = str(order.get("side", "N/A")).upper()
            qty = _finite_number(order.get("quantity"))
            price = _finite_number(order.get("price"))
            qty_text = f"{qty:.4f}" if qty is not None else "n/a"
            price_text = f"${price:,.2f}" if price is not None else "n/a"
            side_color = Colors.POS if side == "BUY" else Colors.NEG
            print(
                f"  {ts[-5:]:<8} {ticker:<6} {side_color}{side:<6}{Colors.ENDC} "
                f"{qty_text:>7} {price_text:>9}"
            )
    else:
        print(f"  {Colors.MUTED}Nessun ordine eseguito nel ciclo persistito.{Colors.ENDC}")

    print_header("SYSTEM STATUS")
    print(f"  System snapshot: {'fresh' if account_fresh else 'unavailable/stale'}")
    print(f"{Colors.TITLE}Tickers:{Colors.ENDC} {len(tickers)}")
    print(f"{Colors.TITLE}Dashboard refresh:{Colors.ENDC} 30 seconds")
    print(f"{Colors.TITLE}Configured capital:{Colors.ENDC} n/a (not persisted)")
    print(f"{Colors.TITLE}Environment:{Colors.ENDC} {mode}")
    print(f"{Colors.TITLE}Quote feed:{Colors.ENDC} {'available' if any(quote_sources) else 'unavailable'}")

    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.ACCENT}Next refresh in 30 seconds...{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 72}{Colors.ENDC}\n")


if __name__ == "__main__":
    try:
        while True:
            render()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nStopped.")
