"""Vertical OPG integration test using the real bot and BrokerExecutor with a fake broker."""

import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

import trade
from src.execution.broker_executor import BrokerExecutor
from src.strategy.risk_overlay import RiskOverlayModule


class FakePaperTradingClient:
    """Deterministic Alpaca boundary; never performs network or broker operations."""

    def __init__(self):
        self.position_qty = 0
        self.order_status = "accepted"
        self.submitted_requests = []
        self.order_id = "fake-paper-order-001"
        self.cancel_calls = 0
        self.replace_calls = 0
        self.close_calls = 0

    def get_account(self):
        return SimpleNamespace(
            id="fake-paper-account",
            status="active",
            trading_blocked=False,
            cash="1000",
            portfolio_value="1000",
            buying_power="1000",
            equity="1000",
            daytrade_count=0,
        )

    def get_all_positions(self):
        if self.position_qty == 0:
            return []
        return [
            SimpleNamespace(
                symbol="AAPL",
                qty=str(self.position_qty),
                market_value=str(self.position_qty * 101),
                avg_entry_price="101",
                unrealized_pl="0",
            )
        ]

    def get_asset(self, symbol):
        assert symbol == "AAPL"
        return SimpleNamespace(
            symbol=symbol,
            asset_class="us_equity",
            status="active",
            tradable=True,
            attributes=[],
            fractionable=True,
            shortable=True,
        )

    def submit_order(self, request):
        self.submitted_requests.append(request)
        return self._order(request)

    def _order(self, request=None):
        if request is None:
            return SimpleNamespace(
                id=self.order_id,
                client_order_id=self.submitted_requests[0].client_order_id,
                symbol="AAPL",
                side="buy",
                qty="2",
                type="market",
                time_in_force="opg",
                status=self.order_status,
                filled_qty="2" if self.order_status == "filled" else "0",
                filled_avg_price="101" if self.order_status == "filled" else None,
            )
        return SimpleNamespace(
            id=self.order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side.value,
            qty=str(request.qty),
            type="market",
            time_in_force=request.time_in_force.value,
            status=self.order_status,
            filled_qty="0",
            filled_avg_price=None,
        )

    def get_order_by_id(self, order_id):
        assert order_id == self.order_id
        return self._order()

    def get_order_by_client_id(self, client_order_id):
        assert self.submitted_requests
        assert client_order_id == self.submitted_requests[0].client_order_id
        return self._order()

    def get_orders(self, *, filter):
        return []

    def get(self, path, params):
        assert path == "/account/activities"
        return []

    def cancel_order_by_id(self, *_args, **_kwargs):
        self.cancel_calls += 1
        raise AssertionError("vertical test must never cancel broker orders")

    def replace_order_by_id(self, *_args, **_kwargs):
        self.replace_calls += 1
        raise AssertionError("vertical test must never replace broker orders")

    def close_position(self, *_args, **_kwargs):
        self.close_calls += 1
        raise AssertionError("vertical test must never close broker positions")


def _real_executor_with_fake_broker(state_file: Path, broker: FakePaperTradingClient):
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.api_key = "fake"
    executor.api_secret = "fake"
    executor.base_url = "https://paper-api.alpaca.markets"
    executor.paper_trading = True
    executor.initial_capital = 1000.0
    executor.configured_symbols = ("AAPL",)
    executor.logger = logging.getLogger(__name__)
    executor.trading_client = broker
    executor.metrics = SimpleNamespace(get_all_metrics=lambda: {})
    executor.state_file = state_file
    executor._persisted_state_revision = -1
    executor._state_persistence_failed = False
    executor._execution_lock_local = threading.local()
    executor.execution_lock_timeout_seconds = 2.0
    executor._current_account_identity_verified = False
    executor.state = {
        "account_mode": "paper",
        "account_id": None,
        # Model a previously initialized account with a verified, account-scoped
        # high-water mark. The risk gate must not be bypassed or re-anchored.
        "risk_peak_portfolio_value": 1000.0,
        "risk_peak_equity_source": "alpaca_account",
        "risk_peak_account_mode": "paper",
        "risk_peak_account_id": "fake-paper-account",
        "opg_batches": {},
        "order_submission_unresolved": {},
        "order_submission_confirmed": {},
        "emergency_liquidation_pending": {},
        "emergency_liquidation_unresolved": {},
    }
    return executor


def test_opg_vertical_flow_from_ppo_inference_through_persisted_fill_reconciliation(
    tmp_path, monkeypatch
):
    broker = FakePaperTradingClient()
    executor = _real_executor_with_fake_broker(
        tmp_path / "broker_state.json", broker
    )

    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.tickers = ["AAPL"]
    bot.executor = executor
    bot.pipeline = SimpleNamespace(
        risk_module=RiskOverlayModule(
            max_position_size=0.20,
            max_drawdown_threshold=0.15,
            vix_threshold_high=30.0,
            vix_threshold_low=15.0,
            initial_capital=1000.0,
            max_gross_exposure=0.50,
        ),
        current_portfolio_value=1000.0,
    )
    bot.liquidate_on_drawdown = False
    bot.get_vix_value = lambda: 10.0
    bot.ppo_agent = SimpleNamespace(
        predict_weights=lambda market_data, current_weights: np.array([0.20]),
        max_position_size=0.20,
        max_gross_exposure=0.50,
    )
    # Keep the corporate-action input at the fake broker boundary; the actual
    # PPO weight generation, risk overlay, sizing, preflight and executor remain active.
    executor.reconcile_corporate_actions = lambda symbols, positions=None: {
        "safe_for_trading": True
    }
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    market_data = pd.DataFrame(
        {"Close_AAPL": [99.0, 100.0]},
        index=pd.DatetimeIndex(["2026-11-02", "2026-11-03"]),
    )
    weights = bot.generate_weights(market_data)
    result = bot.execute_trade(
        weights,
        opg_context={
            "market_data": market_data,
            "signal_bar_at": "2026-11-03T00:00:00+00:00",
            "target_session": "2026-11-04",
        },
    )

    assert result["execution_mode"] == "opg"
    assert result["orders"][0]["ticker"] == "AAPL"
    assert result["orders"][0]["quantity"] == 2
    assert result["orders"][0]["time_in_force"] == "opg"
    assert len(broker.submitted_requests) == 1
    request = broker.submitted_requests[0]
    assert request.symbol == "AAPL"
    assert request.qty == 2
    assert request.side.value == "buy"
    assert request.time_in_force.value == "opg"

    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    batch_id = result["opg_batch"]["batch_id"]
    assert persisted["opg_batches"][batch_id]["preflight"]["approved"] is True
    assert persisted["opg_batches"][batch_id]["status"] == "submission_complete"

    # Simulate a broker fill after submission, then run the real read-only recovery.
    broker.order_status = "filled"
    broker.position_qty = 2
    executor.get_account_status()
    recovery = executor.reconcile_opg_batches_read_only()

    assert recovery["batches_completed"] == 1
    assert recovery["batches_blocked"] == 0
    recovered = json.loads(executor.state_file.read_text(encoding="utf-8"))
    final_batch = recovered["opg_batches"][batch_id]
    assert final_batch["status"] == "completed"
    # Reconciliation persists normalized quantities as JSON numbers, not strings.
    assert final_batch["orders"]["AAPL"]["filled_qty"] == 2
    assert final_batch["orders"]["AAPL"]["position_reconciled"] is True
    assert len(broker.submitted_requests) == 1
    assert broker.cancel_calls == 0
    assert broker.replace_calls == 0
    assert broker.close_calls == 0
