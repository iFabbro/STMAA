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
    assert result["equity_close"] == pytest.approx(10197)


class FakeFeed:
    def __init__(self):
        self.sessions = ["2026-01-02"]

    @property
    def session(self):
        return self.sessions[-1]

    @session.setter
    def session(self, value):
        if value not in self.sessions:
            self.sessions.append(value)
            self.sessions.sort()

    def get_bars(self, tickers, days):
        data = {}
        for t in tickers:
            data[f"Open_{t}"] = [100.] * len(self.sessions)
            data[f"Close_{t}"] = [101.] * len(self.sessions)
            for feature in ("RSI", "MACD", "BB_middle", "ATR"):
                data[f"{feature}_{t}"] = [50. if feature == "RSI" else 0.] * len(self.sessions)
        return pd.DataFrame(data, index=pd.DatetimeIndex(self.sessions))


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


def test_transition_is_not_replayed_if_inference_fails_after_transition(tmp_path):
    class FailingAgent:
        def predict_weights(self, frame, current_weights=None):
            raise RuntimeError("inference unavailable")
    feed, path = FakeFeed(), tmp_path / "ledger.jsonl"
    collector = PPOShadowCollector(FakeAgent(), feed, path, ["AAA", "BBB"], initial_capital=10000)
    collector.collect_once()
    feed.session = "2026-01-05"
    failing = PPOShadowCollector(FailingAgent(), feed, path, ["AAA", "BBB"], initial_capital=10000)
    with pytest.raises(RuntimeError, match="inference unavailable"):
        failing.collect_once()
    assert replay_shadow_ledger(path)["transition_count"] == 1
    # Retry same session: the old target has already been consumed, so no duplicate fill.
    retried = collector.collect_once()
    assert retried["executed_pending_target"] is False
    assert replay_shadow_ledger(path)["transition_count"] == 1


def test_collector_rejects_non_advancing_session(tmp_path):
    feed = FakeFeed()
    collector = PPOShadowCollector(FakeAgent(), feed, tmp_path / "ledger.jsonl", ["AAA", "BBB"])
    collector.collect_once()
    feed.sessions = ["2026-01-01"]
    with pytest.raises(ValueError, match="outside the supplied market-data window"):
        collector.collect_once()


def test_collector_rejects_skipped_market_sessions(tmp_path):
    feed, path = FakeFeed(), tmp_path / "ledger.jsonl"
    collector = PPOShadowCollector(FakeAgent(), feed, path, ["AAA", "BBB"])
    collector.collect_once()
    feed.session = "2026-01-05"
    feed.session = "2026-01-06"
    with pytest.raises(ValueError, match="Missed one or more NYSE sessions"):
        collector.collect_once()


def test_shadow_modules_do_not_import_or_call_broker_execution_clients():
    paths = [Path("src/agents/ppo_shadow_collector.py"), Path("src/agents/ppo_shadow_ledger.py"),
             Path("src/agents/ppo_shadow_portfolio.py"), Path("src/agents/ppo_shadow_replay.py"),
             Path("src/data/alpaca_feed.py")]
    forbidden_modules = {"trade", "alpaca.trading", "alpaca.trading.client"}
    forbidden_calls = {"submit_order", "submit_orders", "replace_order", "cancel_order",
                       "cancel_orders", "close_position", "close_all_positions",
                       "get_account", "get_orders", "get_all_positions", "get_open_position"}
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            assert not any(n == f or n.startswith(f + ".") for n in names for f in forbidden_modules), str(path)
            if isinstance(node, ast.Call):
                called = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                assert called not in forbidden_calls, f"{called} in {path}"
