import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def alpaca_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def test_executor_creation_structure(alpaca_env):
    executor = BrokerExecutor(paper_trading=True)

    assert executor.paper_trading is True
    assert executor.state_file.exists()


def test_execute_weights_mock(alpaca_env, monkeypatch):
    executor = BrokerExecutor()

    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 500000.0,
            "portfolio_value": 500000.0,
            "buying_power": 500000.0,
            "equity": 500000.0,
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {
            "AAPL": 100.0,
            "GOOGL": 200.0,
            "MSFT": 300.0,
        },
    )

    weights = PortfolioWeights(
        weights=np.array([0.4, 0.3, 0.3]),
        tickers=["AAPL", "GOOGL", "MSFT"],
    )

    result = executor.execute_weights(weights)

    assert "orders" in result
    assert len(result["orders"]) == 3
    assert result["timestamp"] is not None


class _FakeOrder:
    def __init__(self, status, qty=0, filled_qty=0, limit_price=None, notional=None, symbol="AAPL"):
        self.status = status
        self.qty = qty
        self.filled_qty = filled_qty
        self.limit_price = limit_price
        self.notional = notional
        self.symbol = symbol


def test_reserved_notional_counts_accepted(alpaca_env):
    executor = BrokerExecutor()
    orders = [_FakeOrder("accepted", qty=10, filled_qty=0, limit_price=100.0, symbol="AAPL")]
    reserved = executor._get_reserved_notional(orders, prices={"AAPL": 100.0})
    assert reserved == 1000.0


def test_reserved_notional_counts_partially_filled_residual(alpaca_env):
    executor = BrokerExecutor()
    orders = [_FakeOrder("partially_filled", qty=10, filled_qty=4, limit_price=50.0, symbol="MSFT")]
    reserved = executor._get_reserved_notional(orders, prices={"MSFT": 50.0})
    assert reserved == 300.0


def test_reserved_notional_ignores_filled(alpaca_env):
    executor = BrokerExecutor()
    orders = [_FakeOrder("filled", qty=10, filled_qty=10, limit_price=50.0, symbol="GOOGL")]
    reserved = executor._get_reserved_notional(orders, prices={"GOOGL": 50.0})
    assert reserved == 0.0


def test_execute_weights_uses_residual_budget(alpaca_env, monkeypatch):
    executor = BrokerExecutor()

    executor.state["risk_peak_portfolio_value"] = 1000.0
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 500000.0,
            "portfolio_value": 1000.0,
            "buying_power": 1000.0,
            "equity": 1000.0,
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [_FakeOrder("accepted", qty=2, filled_qty=0, limit_price=100.0, symbol="MSFT")],
    )
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {"AAPL": 100.0},
    )

    weights = PortfolioWeights(weights=np.array([1.0]), tickers=["AAPL"])
    result = executor.execute_weights(weights)

    assert len(result["orders"]) == 1
    assert result["orders"][0]["status"] == "failed"


def test_risk_gate_blocks_on_drawdown(alpaca_env):
    executor = BrokerExecutor()
    executor.state["peak_equity"] = 1000.0
    capped, block = executor._risk_gate(800.0, 500.0)
    assert capped == 0.0
    assert block and block.startswith("max_drawdown")


def test_risk_gate_caps_exposure(alpaca_env):
    executor = BrokerExecutor()
    executor.state["peak_equity"] = 1000.0
    capped, block = executor._risk_gate(970.0, 800.0)
    assert capped <= 500.0
    assert block is None


def test_risk_engine_limits_position(alpaca_env):
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.10, max_exposure_pct=0.50)
    allowed, block = engine.allow("AAPL", 2000.0, 10000.0, {"AAPL": {"market_value": 0.0}}, reserved_notional=0.0)
    assert allowed == 1000.0
    assert block is None


def test_risk_engine_blocks_when_no_budget(alpaca_env):
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.10, max_exposure_pct=0.50)
    allowed, block = engine.allow("AAPL", 1000.0, 10000.0, {"AAPL": {"market_value": 0.0}}, reserved_notional=6000.0)
    assert allowed == 0.0
    assert block == "risk_block"


def test_risk_engine_limits_symbol_concentration(alpaca_env):
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.10, max_exposure_pct=0.50, max_symbol_concentration_pct=0.05)
    allowed, block = engine.allow("AAPL", 2000.0, 10000.0, {"AAPL": {"market_value": 0.0}}, reserved_notional=0.0)
    assert allowed == 500.0
    assert block is None


def test_risk_engine_blocks_when_total_exposure_breached(alpaca_env):
    """Ordini riservati (4500) + posizioni aperte (3000) superano il 50% (5000): blocco."""
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.10, max_exposure_pct=0.50, max_symbol_concentration_pct=0.05)
    allowed, block = engine.allow("AAPL", 1000.0, 10000.0, {"AAPL": {"market_value": 0.0}, "MSFT": {"market_value": 3000.0}}, reserved_notional=4500.0)
    assert allowed == 0.0
    assert block == "risk_block"


def test_risk_engine_open_positions_reduce_exposure_budget(alpaca_env):
    """Le posizioni gia' aperte (4500) lasciano solo 500 di budget sul 50% di 10000."""
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.10, max_exposure_pct=0.50)
    positions = {"AAPL": {"market_value": 0.0}, "MSFT": {"market_value": 4500.0}}
    allowed, block = engine.allow("AAPL", 1000.0, 10000.0, positions, reserved_notional=0.0)
    assert allowed == 500.0
    assert block is None


def test_risk_engine_low_open_positions_do_not_limit(alpaca_env):
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.10, max_exposure_pct=0.50)
    positions = {"AAPL": {"market_value": 0.0}, "MSFT": {"market_value": 3000.0}}
    allowed, block = engine.allow("AAPL", 1000.0, 10000.0, positions, reserved_notional=0.0)
    assert allowed == 1000.0
    assert block is None


def test_risk_engine_existing_position_is_returned_to_budget(alpaca_env):
    """target_notional sostituisce la posizione esistente: il suo valore torna al budget."""
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.20, max_exposure_pct=0.50)
    positions = {"AAPL": {"market_value": 1000.0}, "MSFT": {"market_value": 3500.0}}
    allowed, block = engine.allow("AAPL", 2000.0, 10000.0, positions, reserved_notional=0.0)
    assert allowed == 1500.0  # 5000 - 4500 + 1000
    assert block is None


def test_risk_engine_short_position_uses_absolute_value(alpaca_env):
    from src.execution.broker_executor import RiskEngine
    engine = RiskEngine(max_position_pct=0.20, max_exposure_pct=0.50)
    positions = {"AAPL": {"market_value": -1000.0}, "MSFT": {"market_value": 3500.0}}
    allowed, block = engine.allow("AAPL", 2000.0, 10000.0, positions, reserved_notional=0.0)
    assert allowed == 1500.0
    assert block is None


def test_portfolio_manager_scales_weights(alpaca_env):
    from src.execution.broker_executor import PortfolioManager
    from src.strategy.weight_interface import PortfolioWeights
    import numpy as np
    pm = PortfolioManager(max_total_exposure_pct=0.50, max_symbol_count=10)
    weights = PortfolioWeights(weights=np.array([0.6, 0.4]), tickers=["AAPL", "MSFT"])
    scaled, block = pm.allocate(weights, 10000.0, {"AAPL": {"market_value": 1000.0}}, reserved_notional=4000.0)
    assert block is None
    assert round(float(scaled.weights.sum()), 6) <= 1.0


def test_portfolio_manager_blocks_too_many_symbols(alpaca_env):
    from src.execution.broker_executor import PortfolioManager
    from src.strategy.weight_interface import PortfolioWeights
    import numpy as np
    pm = PortfolioManager(max_total_exposure_pct=0.50, max_symbol_count=1)
    weights = PortfolioWeights(weights=np.array([1.0, 0.0]), tickers=["AAPL", "MSFT"])
    scaled, block = pm.allocate(weights, 10000.0, {}, reserved_notional=0.0)
    assert block == "max_symbol_count"


def test_risk_gate_initializes_from_verified_account_equity_not_configured_capital(alpaca_env):
    executor = BrokerExecutor(initial_capital=500000.0)
    executor.state.pop("peak_equity", None)
    executor.state.pop("risk_peak_portfolio_value", None)

    capped, block = executor._risk_gate(100000.0, 50000.0)

    assert block is None
    assert capped == pytest.approx(50000.0)
    assert executor.state["risk_peak_portfolio_value"] == pytest.approx(100000.0)


def test_risk_gate_ignores_unverified_legacy_peak_and_prefers_verified_peak(alpaca_env):
    executor = BrokerExecutor(initial_capital=500000.0)
    executor.state["peak_equity"] = 500000.0  # legacy value without provenance
    executor.state["risk_peak_portfolio_value"] = 100000.0

    capped, block = executor._risk_gate(90000.0, 40000.0)

    assert block is None
    assert capped == pytest.approx(40000.0)
    assert executor.state["peak_equity_source"] == "verified_account"


def test_explicit_liquidation_cancels_open_orders_and_closes_long_and_short_positions(alpaca_env):
    from types import SimpleNamespace

    class FakeTradingClient:
        def __init__(self):
            self.cancelled = []
            self.closed = []

        def cancel_order_by_id(self, order_id):
            self.cancelled.append(order_id)

        def get_all_positions(self):
            return [
                SimpleNamespace(symbol="AAPL", qty="3"),
                SimpleNamespace(symbol="TSLA", qty="-2"),
                SimpleNamespace(symbol="ZERO", qty="0"),
            ]

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id=f"close-{symbol}", status="accepted")

    executor = BrokerExecutor()
    fake = FakeTradingClient()
    executor.trading_client = fake
    executor._get_open_orders = lambda: []

    orders = executor._liquidate_all_positions(
        [SimpleNamespace(id="open-1"), SimpleNamespace(id="open-2")]
    )

    assert fake.cancelled == ["open-1", "open-2"]
    assert fake.closed == ["AAPL", "TSLA"]
    assert [order["ticker"] for order in orders] == ["AAPL", "TSLA"]
    assert [order["quantity"] for order in orders] == [3.0, 2.0]


def test_explicit_liquidation_aborts_if_open_order_cannot_be_cancelled(alpaca_env):
    from types import SimpleNamespace

    class FakeTradingClient:
        def cancel_order_by_id(self, order_id):
            raise RuntimeError("cancel failed")

        def get_all_positions(self):
            raise AssertionError("Must not query/close positions after failed cancellation")

    executor = BrokerExecutor()
    executor.trading_client = FakeTradingClient()

    with pytest.raises(RuntimeError, match="cancel failed"):
        executor._liquidate_all_positions([SimpleNamespace(id="open-1")])

def test_explicit_liquidation_waits_for_open_orders_to_clear(alpaca_env):
    from types import SimpleNamespace

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def cancel_order_by_id(self, order_id):
            return None

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="3")]

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="close", status="accepted")

    executor = BrokerExecutor()
    fake = FakeTradingClient()
    executor.trading_client = fake
    executor._get_open_orders = lambda: [SimpleNamespace(id="still-open")]

    with pytest.raises(RuntimeError, match="broker confirms all open orders"):
        executor._liquidate_all_positions([SimpleNamespace(id="open-1")])
    assert fake.closed == []





def test_get_positions_fails_closed_when_broker_api_is_unavailable(alpaca_env):
    class FailingTradingClient:
        def get_all_positions(self):
            raise ConnectionError("broker API unavailable")

    executor = BrokerExecutor()
    executor.trading_client = FailingTradingClient()

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.get_positions()


def test_execute_weights_does_not_submit_orders_when_positions_are_unreadable(
    alpaca_env, monkeypatch
):
    class FakeTradingClient:
        def __init__(self):
            self.submitted = []

        def submit_order(self, request):
            self.submitted.append(request)
            raise AssertionError("No order may be submitted when positions are unreadable")

    executor = BrokerExecutor()
    fake_client = FakeTradingClient()
    executor.trading_client = fake_client
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 10000.0,
            "portfolio_value": 10000.0,
            "buying_power": 10000.0,
            "equity": 10000.0,
            "account_data_fresh": True,
        },
    )
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Unable to retrieve broker positions; refusing to execute trades")
        ),
    )

    weights = PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.execute_weights(weights)

    assert fake_client.submitted == []



def test_execute_weights_holds_position_when_target_is_unchanged(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 9000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": 1000.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request) or SimpleNamespace(id="unexpected", status="accepted"),
    )

    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
    )

    assert result["orders"] == []
    assert submitted == []


def test_execute_weights_sells_only_delta_when_target_is_reduced(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 9000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": 1000.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="delta-sell", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.05]), tickers=["AAPL"])
    )

    assert len(result["orders"]) == 1
    assert submitted[0].qty == pytest.approx(5.0)
    assert str(submitted[0].side).lower().endswith("sell")


def test_execute_weights_zero_target_closes_position(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 9000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": 200.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="close-position", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.0]), tickers=["AAPL"])
    )

    assert len(result["orders"]) == 1
    assert submitted[0].qty == pytest.approx(2.0)
    assert str(submitted[0].side).lower().endswith("sell")


def test_execute_weights_skips_symbol_with_existing_open_order(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 9000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [SimpleNamespace(symbol="AAPL", qty="10", filled_qty="0",
                                 limit_price=None, notional=None, status="accepted")],
    )
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request) or SimpleNamespace(id="unexpected", status="accepted"),
    )

    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
    )

    assert result["orders"] == []
    assert submitted == []


def test_drawdown_risk_gate_does_not_implicitly_liquidate_positions(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 7500.0, "portfolio_value": 8000.0,
        "buying_power": 8000.0, "equity": 8000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": 500.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request) or SimpleNamespace(id="unexpected", status="accepted"),
    )

    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.0]), tickers=["AAPL"])
    )

    assert result["orders"] == []
    assert submitted == []


def test_portfolio_manager_symbol_limit_blocks_execution(alpaca_env, monkeypatch):
    tickers = [f"T{i}" for i in range(11)]
    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars",
        lambda requested: {ticker: 100.0 for ticker in requested},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: pytest.fail("No order should be submitted when symbol limit blocks execution"),
    )

    result = executor.execute_weights(
        PortfolioWeights(weights=np.full(len(tickers), 1.0 / len(tickers)), tickers=tickers)
    )

    assert result["orders"] == []


def test_multi_symbol_rebalance_reserves_submitted_orders_against_gross_exposure(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    tickers = [f"T{i}" for i in range(10)]
    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars",
        lambda requested: {ticker: 100.0 for ticker in requested},
    )
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id=f"order-{len(submitted)}", status="filled")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.full(len(tickers), 0.1), tickers=tickers)
    )

    assert len(result["orders"]) == 5
    total_submitted_notional = sum(order.qty * 100.0 for order in submitted)
    assert total_submitted_notional <= 5000.0 + 1e-6


def test_buy_orders_are_capped_to_verified_buying_power(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 500.0, "portfolio_value": 10000.0,
        "buying_power": 500.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="buy-limited", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
    )

    assert len(result["orders"]) == 1
    assert submitted[0].qty == pytest.approx(5.0)


def test_zero_buying_power_still_allows_risk_reducing_sell(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 0.0, "portfolio_value": 10000.0,
        "buying_power": 0.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": 200.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="sell-without-cash", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.0]), tickers=["AAPL"])
    )

    assert len(result["orders"]) == 1
    assert submitted[0].qty == pytest.approx(2.0)
    assert str(submitted[0].side).lower().endswith("sell")


def test_pending_risk_reduction_blocks_new_exposure_in_same_batch(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 45000.0, "portfolio_value": 50000.0,
        "buying_power": 50000.0, "equity": 50000.0,
        "account_data_fresh": True,
    })
    positions = {
        "A": {"market_value": 1000.0},
        "B": {"market_value": 500.0},
        "C": {"market_value": 3500.0},
    }
    monkeypatch.setattr(executor, "get_positions", lambda: positions.copy())
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars", lambda tickers: {ticker: 100.0 for ticker in tickers}
    )
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id=f"order-{len(submitted)}", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.01, 0.02, 0.07]), tickers=["A", "B", "C"])
    )

    assert len(result["orders"]) == 1
    assert submitted[0].symbol == "A"
    assert str(submitted[0].side).lower().endswith("sell")


def test_positions_omitted_from_target_weights_are_closed(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 0.0, "portfolio_value": 10000.0,
        "buying_power": 0.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": 1000.0, "qty": 10.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions.copy())
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    requested = []
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: requested.extend(tickers) or {ticker: 100.0 for ticker in tickers},
    )
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="close-omitted", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.0]), tickers=["MSFT"])
    )

    assert requested == ["MSFT", "AAPL"]
    assert len(result["orders"]) == 1
    assert submitted[0].symbol == "AAPL"
    assert submitted[0].qty == pytest.approx(10.0)
    assert str(submitted[0].side).lower().endswith("sell")


def test_zero_buying_power_still_allows_short_cover(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 0.0, "portfolio_value": 10000.0,
        "buying_power": 0.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    positions = {"AAPL": {"market_value": -500.0, "qty": -5.0}}
    monkeypatch.setattr(executor, "get_positions", lambda: positions.copy())
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="cover-short", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)
    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.0]), tickers=["AAPL"])
    )

    assert len(result["orders"]) == 1
    assert submitted[0].qty == pytest.approx(5.0)
    assert str(submitted[0].side).lower().endswith("buy")


def test_reserved_notional_fails_closed_when_active_order_cannot_be_priced(alpaca_env):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    unknown_order = SimpleNamespace(
        status="accepted", qty="10", filled_qty="0",
        limit_price=None, notional=None, symbol="UNKNOWN",
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor._get_reserved_notional([unknown_order], prices={})


def test_reserved_notional_fails_closed_on_invalid_open_order_price(alpaca_env):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    invalid_price_order = SimpleNamespace(
        status="accepted", qty="10", filled_qty="0",
        limit_price="nan", notional=None, symbol="AAPL",
    )

    with pytest.raises(RuntimeError, match="invalid limit_price"):
        executor._get_reserved_notional([invalid_price_order], prices={"AAPL": 100.0})


def test_reserved_notional_accepts_notional_order_without_quantity(alpaca_env):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    notional_order = SimpleNamespace(
        status="accepted", qty=None, filled_qty=None,
        limit_price=None, notional="250.00", symbol="AAPL",
    )

    assert executor._get_reserved_notional([notional_order], prices={}) == pytest.approx(250.0)


def test_reserved_notional_scales_notional_by_remaining_quantity(alpaca_env):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    partially_filled_order = SimpleNamespace(
        status="partially_filled", qty="10", filled_qty="4",
        limit_price=None, notional="1000", symbol="UNKNOWN",
    )

    assert executor._get_reserved_notional([partially_filled_order], prices={}) == pytest.approx(600.0)


def test_execute_weights_aborts_before_orders_if_open_order_exposure_unknown(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [SimpleNamespace(
            status="accepted", qty="10", filled_qty="0",
            limit_price=None, notional=None, symbol="UNKNOWN",
        )],
    )
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {})
    monkeypatch.setattr(
        executor.trading_client, "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_execute_weights_aborts_before_any_order_when_required_price_missing(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="unexpected", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)

    with pytest.raises(RuntimeError, match="valid prices unavailable for: MSFT"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1, 0.1]), tickers=["AAPL", "MSFT"])
        )

    assert submitted == []


@pytest.mark.parametrize(
    "invalid_weights, message",
    [
        (np.array([-0.1, 1.1]), "Negative target weights are unsupported"),
        (np.array([0.7, 0.7]), "gross allocation must not exceed 1"),
    ],
)
def test_execute_weights_rejects_invalid_target_weight_contract(
    alpaca_env, monkeypatch, invalid_weights, message
):
    executor = BrokerExecutor()
    broker_reads = []
    submitted = []
    monkeypatch.setattr(
        executor, "get_account_status",
        lambda: broker_reads.append(True) or {
            "cash": 10000.0, "portfolio_value": 10000.0,
            "buying_power": 10000.0, "equity": 10000.0,
            "account_data_fresh": True,
        },
    )
    monkeypatch.setattr(
        executor.trading_client, "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(ValueError, match=message):
        executor.execute_weights(
            PortfolioWeights(weights=invalid_weights, tickers=["AAPL", "MSFT"])
        )

    assert broker_reads == []
    assert submitted == []
