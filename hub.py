#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent / "src"))

import os
import json
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

HUB_STATE = Path("logs/hub_state.json")


class Colors:
    HEADER    = '\033[94m'
    TITLE     = '\033[36m'
    POS       = '\033[92m'
    NEG       = '\033[91m'
    ACCENT    = '\033[93m'
    MUTED     = '\033[90m'
    ENDC      = '\033[0m'
    BOLD      = '\033[1m'
    UNDERLINE = '\033[4m'


SPARKS = "▁▂▃▄▅▆▇█"


def clear_screen():
    os.system('clear' if os.name == 'posix' else 'cls')


def load_state():
    if HUB_STATE.exists():
        try:
            return json.loads(HUB_STATE.read_text())
        except Exception:
            return {}
    return {}


def sparkline(series, width=48):
    if not series:
        return ""
    vals = series[-width:]
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return SPARKS[0] * len(vals)
    return ''.join(SPARKS[int((x - mn) / (mx - mn) * (len(SPARKS) - 1))] for x in vals)


def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*72}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text:^72}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*72}{Colors.ENDC}\n")


def print_metric(label, value, change=None):
    change_str = ""
    if change is not None:
        color  = Colors.POS if change > 0 else Colors.NEG
        symbol = "↑" if change > 0 else "↓"
        change_str = f" {color}{symbol}{change:+.2f}%{Colors.ENDC}"
    print(f"  {Colors.TITLE}{label:<26}{Colors.ENDC} {Colors.BOLD}{value}{Colors.ENDC}{change_str}")


def get_live_quotes(tickers):
    api_key    = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")

    if not api_key or not api_secret:
        return {t: {"price": 0.0, "change_pct": 0.0} for t in tickers}

    try:
        from src.data.alpaca_feed import AlpacaFeed
        feed = AlpacaFeed(api_key=api_key, api_secret=api_secret)
        return feed.get_latest_quotes(tickers)
    except Exception as e:
        print(f"{Colors.MUTED}  [hub] prezzi live non disponibili: {e}{Colors.ENDC}")
        return {t: {"price": 0.0, "change_pct": 0.0} for t in tickers}


def render():
    state   = load_state()
    tickers = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META']

    last_ts         = state.get("timestamp")
    orders          = state.get("orders", [])
    portfolio_value = float(state.get("portfolio_value", 100000))
    cash            = float(state.get("cash", 50000))
    equity          = float(state.get("equity", portfolio_value))
    equity_curve    = state.get("equity_curve", [portfolio_value * (0.99 + i * 0.0005) for i in range(60)])

    metrics = state.get("metrics", {})
    max_drawdown = metrics.get("max_drawdown", 0)
    sharpe = metrics.get("sharpe_ratio", 0)
    sortino = metrics.get("sortino_ratio", 0)
    calmar = metrics.get("calmar_ratio", 0)
    exposure = metrics.get("exposure", 0)
    regime = metrics.get("regime", "unknown")
    total_return = metrics.get("total_return", 0)
    annualized_return = metrics.get("annualized_return", 0)

    quotes = get_live_quotes(tickers)

    clear_screen()
    print_header("STMAA TRADING HUB")

    print(f"{Colors.TITLE}System:{Colors.ENDC} {Colors.POS}Active{Colors.ENDC}")
    print(f"{Colors.TITLE}Mode:{Colors.ENDC} {Colors.POS}Paper Trading{Colors.ENDC}")
    print(f"{Colors.TITLE}Last update:{Colors.ENDC} {last_ts or datetime.now().isoformat()}")
    print(f"{Colors.TITLE}Orders in last cycle:{Colors.ENDC} {len(orders)}")

    prev_equity = equity_curve[-2] if len(equity_curve) >= 2 else equity
    daily_pnl   = equity - prev_equity
    daily_pct   = (daily_pnl / prev_equity * 100) if prev_equity else 0.0

    print_header("PORTFOLIO METRICS")
    print_metric("Portfolio Value", f"${portfolio_value:,.2f}")
    print_metric("Cash Available",  f"${cash:,.2f}")
    print_metric("Total Equity",    f"${equity:,.2f}")
    print_metric("Daily P&L",       f"${daily_pnl:+,.2f}", daily_pct)
    print_metric("Total Return",    f"{total_return:+.2%}")
    print_metric("Annualized Return", f"{annualized_return:+.2%}")

    print_header("RISK METRICS")
    drawdown_color = Colors.NEG if max_drawdown < -0.1 else Colors.POS
    print(f"  {Colors.TITLE}Max Drawdown:{Colors.ENDC} {drawdown_color}{max_drawdown:.2%}{Colors.ENDC}")
    sharpe_color = Colors.POS if sharpe > 1 else Colors.MUTED
    print(f"  {Colors.TITLE}Sharpe Ratio:{Colors.ENDC} {sharpe_color}{sharpe:.2f}{Colors.ENDC}")
    print(f"  {Colors.TITLE}Sortino Ratio:{Colors.ENDC} {sortino:.2f}")
    print(f"  {Colors.TITLE}Calmar Ratio:{Colors.ENDC} {calmar:.2f}")
    print(f"  {Colors.TITLE}Exposure:{Colors.ENDC} {exposure:.2%}")
    
    regime_color = Colors.POS if regime == 'bull' else Colors.NEG if regime == 'bear' else Colors.ACCENT
    print(f"  {Colors.TITLE}Regime:{Colors.ENDC} {regime_color}{regime.upper()}{Colors.ENDC}")

    print_header("EQUITY CURVE")
    print(f"{Colors.ACCENT}{sparkline(equity_curve, 48)}{Colors.ENDC}")
    print(f"{Colors.MUTED}min={min(equity_curve):,.0f}  max={max(equity_curve):,.0f}  last={equity_curve[-1]:,.0f}{Colors.ENDC}")

    print_header("MONITORED TICKERS")
    print(f"\n{Colors.BOLD}{'Ticker':<10} {'Price':>10} {'Change':>10} {'Source':>8}{Colors.ENDC}")
    print(f"{Colors.UNDERLINE}{'-'*10} {'-'*10} {'-'*10} {'-'*8}{Colors.ENDC}")
    for ticker in tickers:
        q      = quotes.get(ticker, {"price": 0.0, "change_pct": 0.0})
        price  = q["price"]
        change = q["change_pct"]
        color  = Colors.POS if change >= 0 else Colors.NEG
        symbol = "↑" if change >= 0 else "↓"
        source = "live" if price > 0 else "n/a"
        print(
            f"  {Colors.BOLD}{ticker:<10}{Colors.ENDC} "
            f"${price:>8.2f} "
            f"{color}{symbol}{change:>6.2f}%{Colors.ENDC} "
            f"{Colors.MUTED}{source:>8}{Colors.ENDC}"
        )

    print_header("LAST EXECUTIONS")
    print(f"\n{Colors.BOLD}{'Time':<8} {'Tk':<6} {'Side':<6} {'Qty':>7} {'Px':>9}{Colors.ENDC}")
    print(f"{Colors.UNDERLINE}{'-'*8} {'-'*6} {'-'*6} {'-'*7} {'-'*9}{Colors.ENDC}")

    if orders:
        for o in orders[-5:][::-1]:
            ts     = o.get("timestamp", datetime.now().isoformat())[:16].replace("T", " ")
            ticker = o.get("ticker", "N/A")
            side   = str(o.get("side", "N/A")).upper()
            qty    = float(o.get("quantity", 0))
            price  = float(o.get("price", 0))
            side_color = Colors.POS if side == 'BUY' else Colors.NEG
            print(
                f"  {ts[-5:]:<8} "
                f"{Colors.BOLD}{ticker:<6}{Colors.ENDC} "
                f"{side_color}{side:<6}{Colors.ENDC} "
                f"{qty:>7.4f} "
                f"${price:>8.2f}"
            )
    else:
        print(f"  {Colors.MUTED}Nessun ordine eseguito in questo ciclo.{Colors.ENDC}")

    print_header("SYSTEM STATUS")
    print(f"  {Colors.POS}System:{Colors.ENDC} Active")
    print(f"{Colors.TITLE}Tickers:{Colors.ENDC} {len(tickers)}")
    print(f"{Colors.TITLE}Interval:{Colors.ENDC} 30 seconds")
    print(f"{Colors.TITLE}Capital:{Colors.ENDC} $500,000")
    print(f"{Colors.TITLE}Environment:{Colors.ENDC} Paper Trading")
    print(f"{Colors.TITLE}Feed:{Colors.ENDC} Alpaca (live)")

    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*72}{Colors.ENDC}")
    print(f"{Colors.ACCENT}Next refresh in 30 seconds...{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*72}{Colors.ENDC}\n")


if __name__ == "__main__":
    try:
        while True:
            render()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nStopped.")
