"""Command-line entry point for the zero-write Alpaca Paper preflight."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from alpaca.trading.client import TradingClient

from src.utils.paper_preflight import PAPER_ENDPOINT, run_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Alpaca Paper preflight; no broker or local state mutations."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    env = dict(dotenv_values(root / ".env"))
    env.update(os.environ)
    key = env.get("ALPACA_API_KEY", "")
    secret = env.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        print(json.dumps({"overall": "BLOCKED", "reason": "Alpaca credentials are missing"}))
        return 2
    if env.get("STMAA_TRADING_MODE", "paper") != "paper":
        print(json.dumps({"overall": "BLOCKED", "reason": "STMAA_TRADING_MODE must be paper"}))
        return 2
    if env.get("ALPACA_BASE_URL", PAPER_ENDPOINT).rstrip("/") != PAPER_ENDPOINT:
        print(json.dumps({"overall": "BLOCKED", "reason": "ALPACA_BASE_URL must be Alpaca Paper"}))
        return 2

    client = TradingClient(key, secret, paper=True, url_override=PAPER_ENDPOINT)
    report = run_preflight(root, client, env=env)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
