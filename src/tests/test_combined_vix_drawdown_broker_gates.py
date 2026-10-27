"""Combined risk-gate regressions: VIX, drawdown provenance, and broker reconciliation."""
from types import SimpleNamespace

import numpy as np
import pytest

import trade
from src.execution.broker_executor import BrokerExecutor
from src.strategy.risk_overlay import RiskOverlayModule
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def combined_gate_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)


def _build_bot(monkeypatch, *, peak=10000.0, unresolved=False):
    executor = BrokerExecutor(paper_trading=True, initial_capital=10000.0)
    executor.state.update(
        {
            "account_id": "account-A",
            "risk_peak_portfolio_value": peak,
            "risk_peak_equity_source": "alpaca_account",
            "risk_peak_account_mode": "paper",
            "risk_peak_account_id": "account-A",
        }
    )
    if unresolved:
        executor.state["order_submission_unresolved"] = {
            "AAPL": {
                "client_order_id": "submission-ambiguous-AAPL",
                "reason": "submission_outcome_unconfirmed",
            }
        }

    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 5000.0,
            "portfolio_value": 10000.0,
            "equity": 10000.0,
            "buying_power": 5000.0,
            "account_data_fresh": True,
            "account_id": "account-A",
            "account_snapshot_at": "2026-10-27T12:00:00+00:00",
        },
    )
    # Keep the persisted ambiguous submission unresolved, exactly as if the
    # broker response could not be reconciled after a process restart.
    monkeypatch.setattr(executor, "_reconcile_unresolved_submissions", lambda: None)
    monkeypatch.setattr(executor, "_refresh_confirmed_submission_statuses", lambda: None)

    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request)
        or SimpleNamespace(id="unexpected-order", status="accepted"),
    )

    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.initial_capital = 10000.0
    bot.tickers = ["AAPL"]
    bot.liquidate_on_drawdown = False
    bot.executor = executor
    bot.pipeline = SimpleNamespace(
        risk_module=RiskOverlayModule(
            max_drawdown_threshold=0.15,
            initial_capital=10000.0,
        ),
        current_portfolio_value=10000.0,
    )
    weights = PortfolioWeights(
        weights=np.array([0.1]),
        tickers=["AAPL"],
        timestamp="2026-10-27T12:00:00+00:00",
    )
    return bot, executor, weights, submitted


@pytest.mark.parametrize("vix_failure", ["missing", "stale"])
def test_missing_or_stale_vix_blocks_new_exposure_even_with_unreconciled_order(
    combined_gate_env, monkeypatch, vix_failure
):
    bot, _, weights, submitted = _build_bot(monkeypatch, unresolved=True)

    def unavailable_vix():
        raise RuntimeError(
            f"Cannot obtain a valid VIX close: {vix_failure} observation"
        )

    monkeypatch.setattr(bot, "get_vix_value", unavailable_vix)

    with pytest.raises(RuntimeError, match="Cannot obtain a valid VIX close"):
        bot.execute_trade(weights)

    assert submitted == []


def test_uncomputable_drawdown_blocks_before_vix_or_order_submission(
    combined_gate_env, monkeypatch
):
    bot, _, weights, submitted = _build_bot(
        monkeypatch, peak=float("nan"), unresolved=True
    )
    vix_reads = []
    monkeypatch.setattr(bot, "get_vix_value", lambda: vix_reads.append(True) or 20.0)

    with pytest.raises(RuntimeError, match="Stored risk drawdown peak is invalid"):
        bot.execute_trade(weights)

    assert vix_reads == []
    assert submitted == []


def test_unreconciled_broker_submission_blocks_with_fresh_vix_and_valid_drawdown(
    combined_gate_env, monkeypatch
):
    bot, _, weights, submitted = _build_bot(monkeypatch, unresolved=True)
    monkeypatch.setattr(bot, "get_vix_value", lambda: 20.0)

    with pytest.raises(
        RuntimeError,
        match="Broker order submission requires reconciliation or terminal status",
    ):
        bot.execute_trade(weights)

    assert submitted == []


def test_all_three_risk_uncertainties_combined_never_submit_new_exposure(
    combined_gate_env, monkeypatch
):
    # All three faults coexist. The invalid drawdown provenance must fail closed
    # before VIX can be used or the unresolved broker state can reach submission.
    bot, _, weights, submitted = _build_bot(
        monkeypatch, peak=float("nan"), unresolved=True
    )
    monkeypatch.setattr(
        bot,
        "get_vix_value",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Cannot obtain a valid VIX close: stale observation")
        ),
    )

    with pytest.raises(RuntimeError, match="Stored risk drawdown peak is invalid"):
        bot.execute_trade(weights)

    assert submitted == []
