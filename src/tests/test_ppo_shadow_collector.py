"""Regression tests for shadow ledger, portfolio, replay, and isolation."""
import ast
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from src.agents.ppo_shadow_collector import PPOShadowCollector
from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio
from src.agents.ppo_shadow_replay import replay_shadow_ledger


def test_ledger_hash_chain_rejects_tampering(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = PPOShadowLedger(path)
    ledger.append({"type": "decision", "session": "2026-01-02"})
    ledger.append({"type": "decision", "session": "2026-01-05"})
    assert len(ledger.read()) == 2
    rows = path.read_text().splitlines()
    changed = json.loads(rows[0])
    changed["payload"]["session"] = "tampered"
    path.write_text(json.dumps(changed) + "\n" + rows[1] + "\n")
    with pytest.raises(ValueError, match="integrity"):
        ledger.read()


def test_virtual_portfolio_uses_open_for_rebalance_and_close_for_mark():
    p = PPOShadowPortfolio(("AAA", "BBB"), initial_capital=10000)
    result = p.rebalance_at_open({"AAA": .2, "BBB": 0},
                                 {"AAA": 100, "BBB": 50},
                                 {"AAA": 110, "BBB": 50}, "2026-01-05")
    assert result["equity_open"] == pytest.approx(10000)
    assert result["transaction_cost"] == pytest.approx(3)
    assert result["equity_close"] == pytest.approx(2197) + 8000 - 3


class FakeFeed:
    session = "2026-01-02"
    def get_bars(self, tickers, days):
        data = {}
        for t in tickers:
            data.update({f"Open_{t}": [100.], f"Close_{t}": [101.]})
            data.update({f"{f}_{t}": [50. if f == "RSI" else 0.] for f in ("RSI", "MACD", "BB_middle", "ATR")})
        return pd.DataFrame(data, index=pd.DatetimeIndex([self.session]))


class FakeAgent:
    def predict_weights(self, frame, current_weights=None):
        return np.asarray([.2, .1])


def test_collector_pending_target_replay_and_duplicate_guard(tmp_path):
    feed = FakeFeed()
    path = tmp_path / "ledger.jsonl"
    collector = PPOShadowCollector(FakeAgent(), feed, path, ["AAA", "BBB"], initial_capital=10000)
    assert collector.collect_once()["executed_pending_target"] is False
    assert collector.collect_once()["status"] == "duplicate_session"
    feed.session = "2026-01-05"
    assert collector.collect_once()["executed_pending_target"] is True
    replay = replay_shadow_ledger(path)
    assert replay["decision_count"] == 2
    assert replay["transition_count"] == 1
    assert replay["portfolio"]["last_equity"] > 10000


def test_collector_rejects_non_advancing_session(tmp_path):
    feed = FakeFeed()
    collector = PPOShadowCollector(FakeAgent(), feed, tmp_path / "ledger.jsonl", ["AAA", "BBB"])
    collector.collect_once()
    feed.session = "2026-01-01"
    with pytest.raises(ValueError, match="did not advance"):
        collector.collect_once()


def test_shadow_modules_do_not_import_broker_execution_clients():
    paths = [Path("src/agents/ppo_shadow_collector.py"), Path("src/agents/ppo_shadow_ledger.py"),
             Path("src/agents/ppo_shadow_portfolio.py"), Path("src/agents/ppo_shadow_replay.py")]
    forbidden = {"trade", "alpaca.trading", "alpaca.trading.client"}
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n == f or n.startswith(f + ".") for n in names for f in forbidden), str(path)
