"""Regression tests for shadow ledger, portfolio, replay, and isolation."""
import ast
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace
import src.agents.ppo_shadow_collector as shadow_collector
from src.agents.ppo_shadow_collector import PPOShadowCollector, _agent_provenance
from src.data.market_data_contract import market_data_contract
from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio
from src.agents.ppo_shadow_replay import replay_shadow_ledger
from src.agents.ppo_shadow_snapshots import PPOShadowSnapshotStore
import src.agents.ppo_shadow_evaluator as shadow_evaluator
from src.agents.ppo_shadow_evaluator import evaluate_shadow_holdout



def test_model_provenance_verifies_artifact_manifest_and_training_config(tmp_path):
    model_path = tmp_path / "ppo_trading_model.zip"
    artifact = b"verified-test-model"
    digest = hashlib.sha256(artifact).hexdigest()
    artifact_name = f"ppo_trading_model.{digest}.zip"
    (tmp_path / artifact_name).write_bytes(artifact)
    manifest = {
        "contract_version": 7,
        "tickers": ["AAA", "BBB"],
        "transaction_cost": 0.001,
        "slippage": 0.0005,
        "max_position_size": 0.2,
        "max_gross_exposure": 0.5,
        "action_contract": "long_only_capped_target_weights_zero_floor",
        "execution_contract": "close_signal_next_bar_open_fill",
        "observation_contract": "dimensionless_ohlc_features_v1",
        "market_data_contract": market_data_contract(),
        "model_sha256": digest,
        "artifact_file": artifact_name,
        "training": {"initial_capital": 10000.0, "seed": 42},
    }
    (tmp_path / "ppo_trading_model.zip.metadata.json").write_text(json.dumps(manifest))
    agent = SimpleNamespace(
        inference_available=True, model_path=str(model_path), tickers=["AAA", "BBB"],
        transaction_cost=0.001, slippage=0.0005, max_position_size=0.2,
        max_gross_exposure=0.5, initial_capital=10000.0,
    )
    provenance = _agent_provenance(agent)
    assert provenance["model_sha256"] == digest
    assert provenance["strategy_config"]["initial_capital"] == 10000.0
    (tmp_path / artifact_name).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity"):
        _agent_provenance(agent)


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
        frame = pd.DataFrame(data, index=pd.DatetimeIndex(self.sessions))
        frame.attrs["market_data_contract"] = market_data_contract()
        frame.attrs["market_data_fingerprint"] = hashlib.sha256(frame.to_csv().encode()).hexdigest()
        return frame


class FakeAgent:
    def predict_weights(self, frame, current_weights=None):
        return np.asarray([.2, .1])


TEST_PROVENANCE = {"model_sha256": "a" * 64, "manifest_sha256": "b" * 64,
                   "strategy_config": {}}


def make_collector(monkeypatch, agent, feed, path, authorized=False, initial_capital=100000.0):
    monkeypatch.setattr(shadow_collector, "_agent_provenance", lambda _agent: TEST_PROVENANCE)
    options = dict(initial_capital=initial_capital, collection_authorized=authorized)
    if authorized:
        config = {"tickers": ("AAA", "BBB"), "initial_capital": initial_capital,
                  "transaction_cost": 0.001, "slippage": 0.0005,
                  "max_position_size": 0.2, "max_gross_exposure": 0.5}
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        activation = {"schema": "stmaa_ppo_shadow_activation_v1", "freeze_register_status": "approved",
            "freeze_register_commit": "1" * 40, "collector_commit": "2" * 40,
            "operator_authorization": "approved by test fixture", "approved_at_utc": "2026-01-01T00:00:00Z",
            "first_session": "2026-01-02", "model_sha256": TEST_PROVENANCE["model_sha256"],
            "manifest_sha256": TEST_PROVENANCE["manifest_sha256"], "strategy_config_sha256": digest}
        activation_path = path.with_suffix(".activation.json")
        activation_path.write_text(json.dumps(activation))
        options["activation_record_path"] = activation_path
    return PPOShadowCollector(agent, feed, path, ["AAA", "BBB"], **options)



def test_collection_is_disabled_without_explicit_activation(tmp_path, monkeypatch):
    collector = make_collector(monkeypatch, FakeAgent(), FakeFeed(), tmp_path / "disabled.jsonl")
    with pytest.raises(RuntimeError, match="disabled pending explicit holdout activation"):
        collector.collect_once()
    assert not (tmp_path / "disabled.jsonl").exists()


def test_collector_pending_target_replay_and_duplicate_guard(tmp_path, monkeypatch):
    feed = FakeFeed()
    path = tmp_path / "ledger.jsonl"
    collector = make_collector(monkeypatch, FakeAgent(), feed, path, authorized=True, initial_capital=10000)
    assert collector.collect_once()["executed_pending_target"] is False
    assert collector.collect_once()["status"] == "duplicate_session"
    feed.session = "2026-01-05"
    assert collector.collect_once()["executed_pending_target"] is True
    replay = replay_shadow_ledger(path)
    assert replay["decision_count"] == 2
    assert replay["transition_count"] == 1
    assert replay["portfolio"]["last_equity"] > 10000
    assert replay["benchmark"]["last_equity"] > 10000
    assert replay["provenance"] == TEST_PROVENANCE
    assert replay["last_decision"]["feature_snapshot"]["Close_AAA"] == 101.0
    assert replay["last_decision"]["benchmark_snapshot"]["ticker"] == "SPY"


def test_transition_is_not_replayed_if_inference_fails_after_transition(tmp_path, monkeypatch):
    class FailingAgent:
        def predict_weights(self, frame, current_weights=None):
            raise RuntimeError("inference unavailable")
    feed, path = FakeFeed(), tmp_path / "ledger.jsonl"
    collector = make_collector(monkeypatch, FakeAgent(), feed, path, authorized=True, initial_capital=10000)
    collector.collect_once()
    feed.session = "2026-01-05"
    failing = make_collector(monkeypatch, FailingAgent(), feed, path, authorized=True, initial_capital=10000)
    with pytest.raises(RuntimeError, match="inference unavailable"):
        failing.collect_once()
    assert replay_shadow_ledger(path)["transition_count"] == 1
    # Retry same session: the old target has already been consumed, so no duplicate fill.
    retried = collector.collect_once()
    assert retried["executed_pending_target"] is False
    assert replay_shadow_ledger(path)["transition_count"] == 1


def test_collector_rejects_non_advancing_session(tmp_path, monkeypatch):
    feed = FakeFeed()
    collector = make_collector(monkeypatch, FakeAgent(), feed, tmp_path / "ledger.jsonl", authorized=True)
    collector.collect_once()
    feed.sessions = ["2026-01-01"]
    with pytest.raises(ValueError, match="moved backwards"):
        collector.collect_once()


def test_collector_rejects_skipped_market_sessions(tmp_path, monkeypatch):
    feed, path = FakeFeed(), tmp_path / "ledger.jsonl"
    collector = make_collector(monkeypatch, FakeAgent(), feed, path, authorized=True)
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



def test_activation_requires_structured_approved_record(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_collector, "_agent_provenance", lambda _agent: TEST_PROVENANCE)
    with pytest.raises(ValueError, match="validated activation record"):
        PPOShadowCollector(FakeAgent(), FakeFeed(), tmp_path / "ledger.jsonl", ["AAA", "BBB"],
                           collection_authorized=True)
    collector_path = tmp_path / "bad.activation.json"
    collector_path.write_text(json.dumps({"schema": "stmaa_ppo_shadow_activation_v1",
                                          "freeze_register_status": "draft"}))
    with pytest.raises(ValueError, match="missing a required approval field"):
        PPOShadowCollector(FakeAgent(), FakeFeed(), tmp_path / "ledger.jsonl", ["AAA", "BBB"],
                           collection_authorized=True, activation_record_path=collector_path)


def test_operational_constructor_does_not_accept_injected_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_collector, "_agent_provenance", lambda _agent: TEST_PROVENANCE)
    with pytest.raises(TypeError):
        PPOShadowCollector(FakeAgent(), FakeFeed(), tmp_path / "ledger.jsonl", ["AAA", "BBB"],
                           provenance=TEST_PROVENANCE)


def test_ledger_rejects_partial_trailing_record_without_repair(tmp_path):
    path = tmp_path / "partial.jsonl"
    ledger = PPOShadowLedger(path)
    ledger.append({"type": "header"})
    with path.open("ab") as stream:
        stream.write(b'{"sequence":2')
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Incomplete trailing ledger record"):
        ledger.read()
    with pytest.raises(ValueError, match="Incomplete trailing ledger record"):
        ledger.append({"type": "next"})
    assert path.read_bytes() == original


def test_ledger_reader_and_writer_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "concurrent.jsonl"
    ledger = PPOShadowLedger(path)

    def writer():
        for index in range(40):
            ledger.append({"type": "event", "index": index})

    def reader():
        observed = []
        for _ in range(40):
            observed.append(len(ledger.read()))
        return observed

    with ThreadPoolExecutor(max_workers=2) as pool:
        write_future = pool.submit(writer)
        read_future = pool.submit(reader)
        write_future.result(timeout=10)
        observed = read_future.result(timeout=10)
    assert observed == sorted(observed)
    assert len(ledger.read()) == 40


def test_semantic_replay_rejects_second_decision_without_transition(tmp_path):
    path = tmp_path / "invalid-sequence.jsonl"
    ledger = PPOShadowLedger(path)
    config = {"tickers": ["AAA", "BBB"], "max_position_size": 0.2, "max_gross_exposure": 0.5}
    ledger.append({"type": "run_metadata", "schema": "ppo_shadow_v1",
                   "config": config, "provenance": TEST_PROVENANCE})
    ledger.append({"type": "decision", "session": "2026-01-02",
                   "target_weights": {"AAA": 0.2, "BBB": 0.1}})
    ledger.append({"type": "decision", "session": "2026-01-05",
                   "target_weights": {"AAA": 0.2, "BBB": 0.1}})
    with pytest.raises(ValueError, match="second decision"):
        replay_shadow_ledger(path)


def test_semantic_replay_rejects_transition_not_next_nyse_session(tmp_path):
    path = tmp_path / "invalid-transition.jsonl"
    ledger = PPOShadowLedger(path)
    config = {"tickers": ["AAA", "BBB"], "max_position_size": 0.2, "max_gross_exposure": 0.5}
    ledger.append({"type": "run_metadata", "schema": "ppo_shadow_v1",
                   "config": config, "provenance": TEST_PROVENANCE})
    ledger.append({"type": "decision", "session": "2026-01-02",
                   "target_weights": {"AAA": 0.2, "BBB": 0.1}})
    portfolio = PPOShadowPortfolio(("AAA", "BBB"), initial_capital=10000).to_state()
    ledger.append({"type": "portfolio_transition", "session": "2026-01-06",
                   "signal_session": "2026-01-02", "portfolio": portfolio,
                   "metrics": {"session": "2026-01-06", "equity_close": 10000},
                   "benchmark": {"ticker": "SPY", "shares": 1, "last_equity": 10000},
                   "benchmark_metrics": {"session": "2026-01-06", "ticker": "SPY",
                                         "equity_close": 10000}})
    with pytest.raises(ValueError, match="next NYSE session"):
        replay_shadow_ledger(path)



def test_snapshot_store_persists_and_verifies_full_inference_frame(tmp_path):
    frame = FakeFeed().get_bars(["AAA", "BBB"], days=100)
    store = PPOShadowSnapshotStore(tmp_path / "ledger.jsonl")
    reference = store.put(frame, "model_inference")
    payload = store.verify(reference)
    assert payload["role"] == "model_inference"
    assert payload["market_data_fingerprint"] == frame.attrs["market_data_fingerprint"]
    restored = pd.read_json(payload["dataframe_json"], orient="table")
    assert list(restored.columns) == list(frame.columns)
    assert restored.iloc[-1].to_dict() == frame.iloc[-1].to_dict()
    artifact = store.root / reference["path"].split("/")[-1]
    artifact.write_text("{}")
    with pytest.raises(ValueError, match="integrity verification"):
        store.verify(reference)


def test_holdout_evaluator_waits_for_exact_complete_window_and_is_one_shot(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shadow_evaluator, "HOLDOUT_RETURN_SESSIONS", 2)
    monkeypatch.setattr(shadow_evaluator, "HOLDOUT_DECISION_COUNT", 3)
    feed, path = FakeFeed(), tmp_path / "holdout.jsonl"
    collector = make_collector(
        monkeypatch, FakeAgent(), feed, path, authorized=True, initial_capital=10000
    )
    collector.collect_once()
    report_path = tmp_path / "holdout.final_report.json"
    with pytest.raises(ValueError, match="exactly 2 completed return-bearing sessions"):
        evaluate_shadow_holdout(path, report_path)
    assert not report_path.exists()

    feed.session = "2026-01-05"
    collector.collect_once()
    with pytest.raises(ValueError, match="exactly 2 completed return-bearing sessions"):
        evaluate_shadow_holdout(path, report_path)
    assert not report_path.exists()

    feed.session = "2026-01-06"
    collector.collect_once()
    report = evaluate_shadow_holdout(path, report_path)
    assert report["status"] == "FINAL"
    assert report["protocol"]["return_bearing_sessions"] == 2
    assert report["protocol"]["decision_count"] == 3
    assert report["first_return_session"] == "2026-01-05"
    assert report["final_return_session"] == "2026-01-06"
    assert report["input_snapshot_evidence"]["verified_references"] == 6
    with pytest.raises(FileExistsError, match="never overwritten"):
        evaluate_shadow_holdout(path, report_path)
