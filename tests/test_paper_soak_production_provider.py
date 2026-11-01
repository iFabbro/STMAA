import pandas as pd
import pytest

from src.strategy.risk_overlay import RiskOverlayModule
from src.strategy.weight_interface import PortfolioWeights
from src.utils.paper_soak_production_provider import (
    ProductionPaperSoakObservationProvider,
)


class FakeExecutor:
    def __init__(self, *, reserved_notional=5_000.0, persistence_healthy=True):
        self.paper_trading = True
        self.base_url = "https://paper-api.alpaca.markets"
        self.state = {
            "account_id": "paper-account-1",
            "account_mode": "paper",
            "state_revision": 4,
            "risk_peak_portfolio_value": 100_000.0,
            "risk_peak_equity_source": "alpaca_account",
            "risk_peak_account_mode": "paper",
            "risk_peak_account_id": "paper-account-1",
            "corporate_action_reconciliation": {
                "last_reconciled_at": "2026-11-01T12:00:00+00:00",
                "unresolved_activity_ids": [],
                "blocked_symbols": [],
            },
        }
        self._current_account_identity_verified = True
        self._state_persistence_failed = not persistence_healthy
        self._persisted_state_revision = 4 if persistence_healthy else 3
        self.reserved_notional = reserved_notional
        self.account = {
            "account_data_fresh": True,
            "status": "active",
            "trading_blocked": False,
            "account_id": "paper-account-1",
            "equity": 100_000.0,
            "account_snapshot_at": "2026-11-01T12:00:00+00:00",
        }
        self.positions = {
            "AAPL": {
                "qty": 10.0,
                "market_value": 10_000.0,
                "avg_entry_price": 900.0,
                "unrealized_pl": 1_000.0,
            }
        }
        self.open_orders = []

    def get_account_status(self):
        return self.account

    def get_positions(self):
        return self.positions

    def _get_open_orders(self):
        return self.open_orders

    def _get_reserved_notional(self, orders, prices=None):
        assert orders is self.open_orders
        assert prices == {"AAPL": 1000.0}
        return self.reserved_notional


class FakeBot:
    def __init__(self, *, reserved_notional=5_000.0, persistence_healthy=True):
        self.executor = FakeExecutor(
            reserved_notional=reserved_notional,
            persistence_healthy=persistence_healthy,
        )
        self.tickers = ["AAPL"]
        self.max_gross_exposure = 0.5
        self.liquidate_on_drawdown = False
        self.pipeline = type(
            "Pipeline",
            (),
            {
                "risk_module": RiskOverlayModule(
                    max_position_size=0.2,
                    max_gross_exposure=0.5,
                    max_drawdown_threshold=0.15,
                    vix_threshold_high=30,
                    vix_threshold_low=15,
                    initial_capital=100_000,
                )
            },
        )()
        self.market_data = pd.DataFrame({"Close_AAPL": [990.0, 1000.0]})
        self.generate_weights_calls = []

    def get_market_data(self):
        return self.market_data

    def generate_weights(
        self, market_data, account_snapshot=None, positions_snapshot=None
    ):
        self.generate_weights_calls.append(
            (market_data, account_snapshot, positions_snapshot)
        )
        assert account_snapshot is self.executor.account
        assert positions_snapshot is self.executor.positions
        return PortfolioWeights(
            weights=[0.2],
            tickers=["AAPL"],
            timestamp="2026-11-01T12:00:00+00:00",
        )

    def get_vix_value(self):
        return 18.0

    def execute_trade(self, *_args, **_kwargs):
        raise AssertionError("shadow provider must never call execute_trade")


def test_production_provider_reuses_production_inference_and_emits_complete_health():
    bot = FakeBot()
    observation = ProductionPaperSoakObservationProvider(bot)()

    assert len(bot.generate_weights_calls) == 1
    market_data, account, positions = bot.generate_weights_calls[0]
    assert market_data is bot.market_data
    assert account is bot.executor.account
    assert positions is bot.executor.positions

    health = observation.health
    assert health.broker_endpoint_is_paper is True
    assert health.order_mutation_attempted is False
    assert health.broker_state_consistent is True
    assert health.exposure_determinable is True
    assert health.market_data_valid is True
    assert health.market_data_fresh is True
    assert health.persistence_healthy is True
    assert health.broker_snapshot_complete is True
    assert health.api_consecutive_failures == 0

    decision = observation.decision
    assert decision["decision_type"] == "production_ppo_shadow"
    assert decision["raw_ppo_weights"] == {"AAPL": 0.2}
    assert decision["pending_order_reserved_notional"] == 5_000.0
    assert decision["gross_exposure_notional"] == 15_000.0
    assert decision["gross_exposure_ratio"] == pytest.approx(0.15)
    assert decision["order_submission"] == "disabled"


def test_provider_marks_broker_state_inconsistent_when_pending_exposure_exceeds_limit():
    bot = FakeBot(reserved_notional=60_000.0)
    observation = ProductionPaperSoakObservationProvider(bot)()

    assert observation.health.exposure_determinable is True
    assert observation.health.broker_state_consistent is False
    assert observation.decision["gross_exposure_ratio"] == pytest.approx(0.7)


def test_provider_fails_closed_when_account_snapshot_is_stale():
    bot = FakeBot()
    bot.executor.account["account_data_fresh"] = False

    with pytest.raises(RuntimeError, match="Fresh Alpaca account snapshot"):
        ProductionPaperSoakObservationProvider(bot)()


def test_provider_fails_closed_when_pending_order_exposure_is_unestimable():
    bot = FakeBot()
    bot.executor._get_reserved_notional = lambda *_args, **_kwargs: float("nan")

    with pytest.raises(RuntimeError, match="Pending-order exposure"):
        ProductionPaperSoakObservationProvider(bot)()


def test_provider_reports_unhealthy_persistence_without_claiming_it_is_healthy():
    bot = FakeBot(persistence_healthy=False)
    observation = ProductionPaperSoakObservationProvider(bot)()

    assert observation.health.persistence_healthy is False
