import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor, RiskEngine, _validate_risk_limits
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def alpaca_env(monkeypatch, tmp_path):
    # Keep each test's broker state isolated; never inherit pending orders from
    # a developer workspace or a previous test run.
    monkeypatch.chdir(tmp_path)
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
    # Explicitly mark the high-water mark as verified. Legacy peak_equity
    # alone is intentionally ignored by _get_peak_equity.
    executor.state["peak_equity"] = 1000.0
    executor.state["risk_peak_portfolio_value"] = 1000.0
    executor.state["peak_equity_source"] = "verified_account"
    capped, block = executor._risk_gate(800.0, 500.0)
    assert capped == 0.0
    assert block and block.startswith("max_drawdown")


def test_risk_gate_caps_exposure(alpaca_env):
    executor = BrokerExecutor()
    executor.state["peak_equity"] = 1000.0
    capped, block = executor._risk_gate(970.0, 800.0)
    assert capped <= 500.0
    assert block is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_portfolio_exposure", 0.0),
        ("max_portfolio_exposure", 1.01),
        ("max_portfolio_exposure", float("nan")),
        ("max_drawdown", 0.0),
        ("max_drawdown", 1.0),
        ("max_drawdown", float("inf")),
    ],
)
def test_account_risk_limits_reject_invalid_values(key, value):
    limits = {"max_portfolio_exposure": 0.50, "max_drawdown": 0.15}
    limits[key] = value
    with pytest.raises(ValueError):
        _validate_risk_limits(limits)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_position_pct": 0.0},
        {"max_position_pct": 1.01},
        {"max_exposure_pct": -0.1},
        {"max_exposure_pct": float("nan")},
        {"max_symbol_concentration_pct": 0.0},
        {"max_symbol_concentration_pct": float("inf")},
    ],
)
def test_risk_engine_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError, match="limit|finite"):
        RiskEngine(**kwargs)


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

    with pytest.raises(RuntimeError, match="broker confirms unrelated open orders are cancelled"):
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

def test_reserved_notional_fails_closed_when_filled_quantity_exceeds_order_quantity(
    alpaca_env,
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    impossible_order = SimpleNamespace(
        status="accepted", qty="1", filled_qty="2",
        limit_price="100", notional=None, symbol="AAPL",
    )

    with pytest.raises(RuntimeError, match="filled_qty exceeds qty"):
        executor._get_reserved_notional([impossible_order], prices={})


def test_execute_weights_preflights_quote_for_risk_forced_position_reduction(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["risk_peak_portfolio_value"] = 10000.0
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 8000.0, "portfolio_value": 10000.0,
        "buying_power": 8000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(
        executor, "get_positions",
        lambda: {"AAPL": {"market_value": 2000.0}},
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {})
    submitted = []

    def submit_order(request):
        submitted.append(request)
        return SimpleNamespace(id="unexpected", status="accepted")

    monkeypatch.setattr(executor.trading_client, "submit_order", submit_order)

    with pytest.raises(RuntimeError, match="valid prices unavailable for: AAPL"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.2]), tickers=["AAPL"])
        )

    assert submitted == []


def test_get_positions_fails_closed_on_non_finite_broker_values(alpaca_env, monkeypatch):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [SimpleNamespace(
            symbol="AAPL", qty="1", market_value="nan",
            avg_entry_price="100", unrealized_pl="0",
        )],
    )

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.get_positions()


def test_explicit_drawdown_liquidation_bypasses_unknown_open_order_exposure(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    open_order = SimpleNamespace(
        id="open-unknown", status="accepted", qty="10", filled_qty="0",
        limit_price=None, notional=None, symbol="UNKNOWN",
    )
    liquidated = []
    submitted = []

    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [open_order])
    monkeypatch.setattr(
        executor, "_liquidate_all_positions",
        lambda orders: liquidated.extend(orders) or [{
            "ticker": "AAPL", "side": "close_position", "quantity": 2.0,
            "order_id": "close-1", "status": "accepted", "price": None,
            "timestamp": "2026-10-23T00:00:00",
        }],
    )
    monkeypatch.setattr(
        executor, "get_positions",
        lambda: (_ for _ in ()).throw(AssertionError("allocation positions gate must be bypassed")),
    )
    monkeypatch.setattr(
        executor, "_get_reserved_notional",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unknown order exposure must not block explicit liquidation")
        ),
    )
    monkeypatch.setattr(executor.trading_client, "submit_order", lambda request: submitted.append(request))
    monkeypatch.setattr(executor, "_save_state", lambda: None)
    monkeypatch.setattr(executor.metrics, "get_all_metrics", lambda: {})
    monkeypatch.setattr(executor.metrics, "write_metrics_json", lambda *args, **kwargs: None)

    result = executor.execute_weights(PortfolioWeights(
        weights=np.array([0.0]), tickers=["AAPL"],
        metadata={
            "circuit_breaker_triggered": True,
            "liquidate_on_circuit_breaker": True,
        },
    ))

    assert liquidated == [open_order]
    assert result["circuit_breaker_triggered"] is True
    assert result["liquidation_requested"] is True
    assert result["orders"][0]["side"] == "close_position"
    assert submitted == []


def test_drawdown_without_explicit_liquidation_does_not_enter_emergency_path(
    alpaca_env, monkeypatch
):
    executor = BrokerExecutor()
    emergency_calls = []
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0, "portfolio_value": 10000.0,
        "buying_power": 10000.0, "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {})
    monkeypatch.setattr(executor, "_get_reserved_notional", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(executor, "_risk_gate", lambda *args: (10000.0, None))
    monkeypatch.setattr(executor, "_liquidate_all_positions", lambda orders: emergency_calls.append(orders))
    monkeypatch.setattr(executor, "_save_state", lambda: None)
    monkeypatch.setattr(executor.metrics, "get_all_metrics", lambda: {})
    monkeypatch.setattr(executor.metrics, "write_metrics_json", lambda *args, **kwargs: None)

    result = executor.execute_weights(PortfolioWeights(
        weights=np.array([0.0]), tickers=["AAPL"],
        metadata={
            "circuit_breaker_triggered": True,
            "liquidate_on_circuit_breaker": False,
        },
    ))

    assert result["circuit_breaker_triggered"] is True
    assert result["liquidation_requested"] is False
    assert emergency_calls == []



def test_emergency_liquidation_resumes_after_partial_close_without_duplicate_close(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    monkeypatch.setattr(executor, "_save_state", lambda: None)
    first_close_order = SimpleNamespace(id="close-AAPL", status="accepted")

    class FakeTradingClient:
        def __init__(self):
            self.closed = []
            self.fail_msft_once = True

        def cancel_order_by_id(self, order_id):
            raise AssertionError("A recorded liquidation order must not be cancelled on retry")

        def get_all_positions(self):
            return [
                SimpleNamespace(symbol="AAPL", qty="3"),
                SimpleNamespace(symbol="MSFT", qty="2"),
            ]

        def close_position(self, symbol):
            self.closed.append(symbol)
            if symbol == "AAPL":
                return first_close_order
            if self.fail_msft_once:
                self.fail_msft_once = False
                raise RuntimeError("MSFT close failed")
            return SimpleNamespace(id="close-MSFT", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    with pytest.raises(RuntimeError, match="MSFT close failed"):
        executor._liquidate_all_positions([])

    assert executor.state["emergency_liquidation_pending"] == {"AAPL": "close-AAPL"}
    assert fake.closed == ["AAPL", "MSFT"]

    # On retry, the broker still reports AAPL's accepted close order as open.
    open_aapl_close = SimpleNamespace(id="close-AAPL", symbol="AAPL")
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [open_aapl_close])
    resumed = executor._liquidate_all_positions([open_aapl_close])

    # The MSFT close raised after submission began. Its outcome is ambiguous,
    # so the persisted unresolved intent must prevent an automatic duplicate.
    # Recovery requires explicit broker reconciliation before retrying MSFT.
    assert fake.closed == ["AAPL", "MSFT"]
    assert resumed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL"
    }
    assert executor.state["emergency_liquidation_unresolved"]["MSFT"][
        "reason"
    ] == "close_submission_outcome_unconfirmed"


def test_emergency_liquidation_aborts_if_broker_does_not_return_close_order_id(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    monkeypatch.setattr(executor, "_save_state", lambda: None)

    class FakeTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def close_position(self, symbol):
            return SimpleNamespace(status="accepted")

    executor.trading_client = FakeTradingClient()
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    with pytest.raises(RuntimeError, match="manual reconciliation required"):
        executor._liquidate_all_positions([])


def test_emergency_liquidation_clears_terminal_pending_order_before_retrying_position(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-old"}
    monkeypatch.setattr(executor, "_save_state", lambda: None)
    fake = SimpleNamespace(
        cancelled=[],
        closed=[],
    )

    class FakeTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="canceled")

        def close_position(self, symbol):
            fake.closed.append(symbol)
            return SimpleNamespace(id="close-new", status="accepted")

    executor.trading_client = FakeTradingClient()
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    orders = executor._liquidate_all_positions([])

    assert fake.closed == ["AAPL"]
    assert orders[0]["order_id"] == "close-new"
    assert executor.state["emergency_liquidation_pending"] == {"AAPL": "close-new"}



def test_emergency_liquidation_keeps_pending_close_when_open_order_snapshot_lags(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-AAPL"}
    monkeypatch.setattr(executor, "_save_state", lambda: None)

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="accepted")

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    # The open-order snapshot temporarily omits a broker-accepted close order.
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    orders = executor._liquidate_all_positions([])

    assert orders == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL"
    }


def test_emergency_liquidation_save_state_failure_after_acceptance_does_not_duplicate_close(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    save_calls = []

    def fail_post_acceptance_save():
        save_calls.append(True)
        # First save persists the unresolved intent before submission; the
        # second save would persist the broker order ID after acceptance.
        if len(save_calls) == 2:
            raise OSError("state disk write failed")

    monkeypatch.setattr(executor, "_save_state", fail_post_acceptance_save)

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="accepted")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="accepted-close-AAPL", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    # First call sees no order in the snapshot; broker accepts close, then
    # persistence fails. On retry, order lookup must protect against duplication.
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    with pytest.raises(OSError, match="state disk write failed"):
        executor._liquidate_all_positions([])

    assert fake.closed == ["AAPL"]
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "accepted-close-AAPL"
    }

    resumed = executor._liquidate_all_positions([])

    assert resumed == []
    assert fake.closed == ["AAPL"]
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "accepted-close-AAPL"
    }
    assert len(save_calls) >= 2



def test_filled_pending_close_is_preserved_while_position_snapshot_is_stale(
    alpaca_env,
):
    """Do not resubmit a close while the broker still reports the old position."""
    import json
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-filled"}
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            # Simulate the positions endpoint lagging behind the filled order.
            return [SimpleNamespace(symbol="AAPL", qty="2")]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="filled")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    executor._get_open_orders = lambda: []

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-filled"
    }
    with open(executor.state_file, encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["emergency_liquidation_pending"] == {
        "AAPL": "close-filled"
    }


def test_terminal_pending_liquidation_is_persisted_when_no_positions_remain(
    alpaca_env,
):
    import json
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-old"}
    executor._save_state()

    class FakeTradingClient:
        def get_all_positions(self):
            return []

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="filled")

    executor.trading_client = FakeTradingClient()
    executor._get_open_orders = lambda: []

    assert executor._liquidate_all_positions([]) == []
    assert executor.state["emergency_liquidation_pending"] == {}
    with open(executor.state_file, encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["emergency_liquidation_pending"] == {}



def test_filled_pending_close_is_retained_until_position_snapshot_is_flat(
    alpaca_env,
):
    import json
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {
        "AAPL": "close-AAPL-filled"
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            # Simulate eventual consistency: the order is filled but the
            # positions endpoint still reports the pre-close quantity.
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="filled")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    executor._get_open_orders = lambda: []

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL-filled"
    }
    with open(executor.state_file, encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL-filled"
    }


@pytest.mark.parametrize(
    "broker_status",
    [
        "new",
        "accepted",
        "partially_filled",
        "pending_new",
        "pending_cancel",
        "pending_replace",
        "held",
        "unknown_future_status",
        "",
    ],
)
def test_emergency_liquidation_preserves_non_terminal_pending_orders(
    alpaca_env, broker_status
):
    """Unknown or non-terminal broker statuses must never trigger duplicate closes."""
    import json
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {
        "AAPL": "close-AAPL-existing"
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="2")]

        def get_order_by_id(self, order_id):
            assert order_id == "close-AAPL-existing"
            return SimpleNamespace(id=order_id, status=broker_status)

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    executor._get_open_orders = lambda: []

    orders = executor._liquidate_all_positions([])

    assert orders == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL-existing"
    }
    with open(executor.state_file, encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL-existing"
    }



@pytest.mark.parametrize("close_outcome", ["missing_order_id", "ambiguous_timeout"])
def test_unresolved_close_intent_survives_restart_without_duplicate_submission(
    alpaca_env, close_outcome
):
    from types import SimpleNamespace

    first_executor = BrokerExecutor()
    first_submissions = []

    class FirstTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def close_position(self, symbol):
            first_submissions.append(symbol)
            if close_outcome == "ambiguous_timeout":
                # Model a broker accepting the request while the response is lost.
                raise TimeoutError("response lost after broker acceptance")
            return SimpleNamespace(status="accepted")

    first_executor.trading_client = FirstTradingClient()
    def no_open_orders():
        return []

    first_executor._get_open_orders = no_open_orders

    expected_error = (
        "manual reconciliation required"
        if close_outcome == "missing_order_id"
        else "response lost after broker acceptance"
    )
    expected_exception = RuntimeError if close_outcome == "missing_order_id" else TimeoutError
    with pytest.raises(expected_exception, match=expected_error):
        first_executor._liquidate_all_positions([])

    assert first_submissions == ["AAPL"]
    assert first_executor.state["emergency_liquidation_unresolved"]["AAPL"][
        "reason"
    ] == "close_submission_outcome_unconfirmed"

    # Simulate a process restart: the next executor must load the persisted intent.
    resumed_executor = BrokerExecutor()
    resumed_submissions = []

    class ResumedTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def close_position(self, symbol):
            resumed_submissions.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    resumed_executor.trading_client = ResumedTradingClient()
    resumed_executor._get_open_orders = lambda: []

    assert resumed_executor.state["emergency_liquidation_unresolved"]["AAPL"]
    assert resumed_executor._liquidate_all_positions([]) == []
    assert resumed_submissions == []
    assert resumed_executor.state["emergency_liquidation_unresolved"]["AAPL"]



def test_emergency_filled_close_stays_pending_until_positions_confirm_flat(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    monkeypatch.setattr(executor, "_save_state", lambda: None)

    class FakeTradingClient:
        def __init__(self):
            self.closed = []
            self.positions = [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_all_positions(self):
            return self.positions

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="filled")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="close-AAPL", status="filled")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    first = executor._liquidate_all_positions([])
    assert first[0]["status"] == "filled"
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-AAPL"
    }
    assert fake.closed == ["AAPL"]

    # The next broker snapshot confirms the position is flat. The filled order
    # is reconciled and must not be submitted again.
    fake.positions = []
    second = executor._liquidate_all_positions([])
    assert second == []
    assert executor.state["emergency_liquidation_pending"] == {}
    assert fake.closed == ["AAPL"]



def test_unresolved_liquidation_reconciles_unique_accepted_broker_order(
    alpaca_env, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    executor = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            assert filter.symbols == ["AAPL"]
            return [SimpleNamespace(
                id="broker-close-1", symbol="AAPL", side="sell", qty="1",
                status="accepted", created_at=created_at + timedelta(seconds=1),
            )]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="accepted")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "broker-close-1"
    }
    assert executor.state["emergency_liquidation_unresolved"] == {}



def test_unresolved_liquidation_reconciles_replacement_child_not_replaced_parent(
    alpaca_env, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    executor = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            return [
                SimpleNamespace(
                    id="old-close", symbol="AAPL", side="sell", qty="1",
                    status="replaced", replaced_by="replacement-close",
                    created_at=created_at + timedelta(seconds=1),
                ),
                SimpleNamespace(
                    id="replacement-close", symbol="AAPL", side="sell", qty="1",
                    status="accepted", replaces="old-close",
                    created_at=created_at + timedelta(seconds=2),
                ),
            ]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="accepted")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "replacement-close"
    }
    assert executor.state["emergency_liquidation_unresolved"] == {}


def test_unresolved_replaced_order_without_visible_child_stays_fail_closed(
    alpaca_env, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    executor = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            return [
                SimpleNamespace(
                    id="old-close", symbol="AAPL", side="sell", qty="1",
                    status="replaced", replaced_by="replacement-not-in-history",
                    created_at=created_at + timedelta(seconds=1),
                )
            ]

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_unresolved"]["AAPL"]
    assert executor.state.get("emergency_liquidation_pending", {}) == {}

def test_unresolved_liquidation_retries_only_after_unique_terminal_rejection(
    alpaca_env, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    executor = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []
            self.position_reads = 0

        def get_all_positions(self):
            self.position_reads += 1
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            return [SimpleNamespace(
                id="broker-close-rejected", symbol="AAPL", side="sell", qty="1",
                status="rejected", created_at=created_at + timedelta(seconds=1),
            )]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="rejected")

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="new-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    # First pass reconciles the ambiguous intent to a unique rejected order.
    # It must defer retry so the next pass can refresh broker positions.
    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_unresolved"] == {}
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "broker-close-rejected"
    }

    # The next pass verifies the terminal order and fresh position snapshot,
    # then retries exactly once against the remaining live position.
    result = executor._liquidate_all_positions([])

    assert fake.closed == ["AAPL"]
    assert result[0]["order_id"] == "new-close"
    assert executor.state["emergency_liquidation_unresolved"] == {}
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "new-close"
    }


@pytest.mark.parametrize("match_count", [0, 2])
def test_unresolved_liquidation_stays_blocked_without_unique_broker_match(
    alpaca_env, monkeypatch, match_count
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    executor = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            return [
                SimpleNamespace(
                    id=f"candidate-{index}", symbol="AAPL", side="sell", qty="1",
                    status="accepted", created_at=created_at + timedelta(seconds=1),
                )
                for index in range(match_count)
            ]

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_unresolved"]["AAPL"]
    assert executor.state.get("emergency_liquidation_pending", {}) == {}



def test_corrupt_persisted_broker_state_fails_closed_without_overwriting_file(
    alpaca_env
):
    from pathlib import Path

    state_path = Path("logs/execution/broker_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    original = '{"emergency_liquidation_pending": '
    state_path.write_text(original, encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing execution with unverified liquidation state"):
        BrokerExecutor()

    assert state_path.read_text(encoding="utf-8") == original


def test_state_write_replace_failure_preserves_previous_persisted_state(
    alpaca_env, monkeypatch
):
    import json
    import src.execution.broker_executor as broker_executor_module

    executor = BrokerExecutor()
    original = executor.state_file.read_text(encoding="utf-8")
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-accepted"}

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(broker_executor_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        executor._save_state()

    assert executor.state_file.read_text(encoding="utf-8") == original
    assert list(executor.state_file.parent.glob(f".{executor.state_file.name}.*.tmp")) == []
    assert json.loads(original).get("emergency_liquidation_pending", {}) == {}


def test_partial_close_order_remains_pending_across_restart_and_repeated_reconciliation(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    first = BrokerExecutor()
    first.state["emergency_liquidation_pending"] = {"AAPL": "close-partial"}
    first._save_state()
    fake = SimpleNamespace(
        closed=[],
        get_all_positions=lambda: [SimpleNamespace(symbol="AAPL", qty="0.4")],
        get_order_by_id=lambda order_id: SimpleNamespace(
            id=order_id, status="partially_filled"
        ),
        close_position=lambda symbol: pytest.fail(
            "Must not submit another close while the prior order is partially filled"
        ),
    )
    first.trading_client = fake
    monkeypatch.setattr(first, "_get_open_orders", lambda: [])
    assert first._liquidate_all_positions([]) == []
    assert first.state["emergency_liquidation_pending"] == {
        "AAPL": "close-partial"
    }

    # Restart from disk and reconcile the same broker snapshot again.
    resumed = BrokerExecutor()
    resumed.trading_client = fake
    monkeypatch.setattr(resumed, "_get_open_orders", lambda: [])
    assert resumed._liquidate_all_positions([]) == []
    assert resumed.state["emergency_liquidation_pending"] == {
        "AAPL": "close-partial"
    }


def test_failed_pre_submission_state_write_never_sends_close_order(
    alpaca_env, monkeypatch
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    fake = SimpleNamespace(
        closed=[],
        get_all_positions=lambda: [SimpleNamespace(symbol="AAPL", qty="1")],
        close_position=lambda symbol: fake.closed.append(symbol),
    )
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor, "_save_state",
        lambda: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(OSError, match="disk unavailable"):
        executor._liquidate_all_positions([])

    assert fake.closed == []

def test_unresolved_liquidation_ignores_matching_order_outside_intent_time_window(
    alpaca_env, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    executor = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    class FakeTradingClient:
        def __init__(self):
            self.closed = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            assert filter.after == created_at - timedelta(seconds=5)
            assert filter.until == created_at + timedelta(minutes=5)
            # Same symbol, side, and quantity, but created well after the
            # intent's reconciliation window: this is not sufficient evidence.
            return [SimpleNamespace(
                id="unrelated-later-order",
                symbol="AAPL",
                side="sell",
                qty="1",
                status="accepted",
                created_at=created_at + timedelta(minutes=30),
            )]

        def close_position(self, symbol):
            self.closed.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    assert executor._liquidate_all_positions([]) == []
    assert fake.closed == []
    assert executor.state["emergency_liquidation_unresolved"]["AAPL"]
    assert executor.state.get("emergency_liquidation_pending", {}) == {}



@pytest.mark.parametrize(
    ("broker_status", "positions", "expected_close_calls", "expected_pending"),
    [
        ("filled", [], [], {}),
        ("canceled", [("AAPL", "1")], ["AAPL"], {"AAPL": "new-close"}),
    ],
)
def test_liquidation_normalizes_enum_status_values(
    alpaca_env, monkeypatch, broker_status, positions, expected_close_calls, expected_pending
):
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "old-close"}
    executor._save_state()
    position_objects = [
        SimpleNamespace(symbol=symbol, qty=qty) for symbol, qty in positions
    ]

    class FakeTradingClient:
        def __init__(self):
            self.close_calls = []

        def get_all_positions(self):
            return position_objects

        def get_order_by_id(self, order_id):
            # Simulate an SDK string-valued Enum: str(status) need not equal status.value.
            return SimpleNamespace(
                id=order_id, status=SimpleNamespace(value=broker_status)
            )

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            return SimpleNamespace(
                id="new-close", status=SimpleNamespace(value="accepted")
            )

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    result = executor._liquidate_all_positions([])

    assert fake.close_calls == expected_close_calls
    assert executor.state["emergency_liquidation_pending"] == expected_pending
    if expected_close_calls:
        assert result[0]["status"] == "accepted"

def test_filled_close_uses_fresh_positions_snapshot_before_clearing_pending(
    alpaca_env, monkeypatch
):
    """A stale empty snapshot must not clear a filled close while residual qty remains."""
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-filled"}
    executor._save_state()
    snapshots = [
        [],
        [SimpleNamespace(symbol="AAPL", qty="0.25")],
    ]

    class FakeTradingClient:
        def __init__(self):
            self.position_calls = 0
            self.close_calls = []

        def get_all_positions(self):
            snapshot = snapshots[self.position_calls]
            self.position_calls += 1
            return snapshot

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="filled")

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    assert executor._liquidate_all_positions([]) == []
    assert fake.position_calls == 2
    assert fake.close_calls == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-filled"
    }


def test_canceled_partial_close_refreshes_residual_position_before_retry(
    alpaca_env, monkeypatch
):
    """A stale flat snapshot must not suppress a retry for a real residual position."""
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-partial-canceled"}
    executor._save_state()
    snapshots = [
        [],
        [SimpleNamespace(symbol="AAPL", qty="0.4")],
    ]

    class FakeTradingClient:
        def __init__(self):
            self.position_calls = 0
            self.close_calls = []

        def get_all_positions(self):
            snapshot = snapshots[self.position_calls]
            self.position_calls += 1
            return snapshot

        def get_order_by_id(self, order_id):
            return SimpleNamespace(
                id=order_id, status="canceled", filled_qty="0.6", qty="1"
            )

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            return SimpleNamespace(id="residual-close", status="accepted")

    fake = FakeTradingClient()
    executor.trading_client = fake
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    result = executor._liquidate_all_positions([])

    assert fake.position_calls == 2
    assert fake.close_calls == ["AAPL"]
    assert result[0]["order_id"] == "residual-close"
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "residual-close"
    }


def test_unresolved_canceled_partial_close_refreshes_positions_before_retry(
    alpaca_env, monkeypatch
):
    """Do not retry against a stale pre-fill quantity after reconciling canceled history."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    first = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    first.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    class FirstTradingClient:
        def __init__(self):
            self.close_calls = []

        def get_all_positions(self):
            # Stale snapshot still reports the original quantity after a partial fill.
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            return [SimpleNamespace(
                id="broker-close-canceled",
                symbol="AAPL",
                side="sell",
                qty="1",
                status="canceled",
                filled_qty="0.6",
                created_at=created_at + timedelta(seconds=1),
            )]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="canceled")

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            pytest.fail("Must defer retry until a fresh position snapshot is available")

    first_client = FirstTradingClient()
    first.trading_client = first_client
    monkeypatch.setattr(first, "_get_open_orders", lambda: [])

    assert first._liquidate_all_positions([]) == []
    assert first_client.close_calls == []
    assert first.state["emergency_liquidation_unresolved"] == {}
    assert first.state["emergency_liquidation_pending"] == {
        "AAPL": "broker-close-canceled"
    }

    # On the next pass/restart, the broker's residual quantity is authoritative.
    resumed = BrokerExecutor()
    resumed_client_calls = []

    class ResumedTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="0.4")]

        def get_order_by_id(self, order_id):
            return SimpleNamespace(id=order_id, status="canceled")

        def close_position(self, symbol):
            resumed_client_calls.append(symbol)
            return SimpleNamespace(id="residual-close", status="accepted")

    resumed.trading_client = ResumedTradingClient()
    monkeypatch.setattr(resumed, "_get_open_orders", lambda: [])

    result = resumed._liquidate_all_positions([])

    assert resumed_client_calls == ["AAPL"]
    assert result[0]["quantity"] == pytest.approx(0.4)
    assert result[0]["order_id"] == "residual-close"
    assert resumed.state["emergency_liquidation_pending"] == {
        "AAPL": "residual-close"
    }


def test_direct_terminal_partial_close_refreshes_residual_after_restart(
    alpaca_env, monkeypatch
):
    """A terminal response may include partial fills; reconcile persisted ID before retry."""
    from types import SimpleNamespace

    first = BrokerExecutor()
    submissions = []

    class FakeTradingClient:
        def __init__(self):
            self.position_reads = 0

        def get_all_positions(self):
            self.position_reads += 1
            if self.position_reads <= 2:
                # The initial snapshot and first reconciliation snapshot are stale.
                return [SimpleNamespace(symbol="AAPL", qty="1")]
            return [SimpleNamespace(symbol="AAPL", qty="0.4")]

        def close_position(self, symbol):
            submissions.append(symbol)
            if len(submissions) == 1:
                return SimpleNamespace(id="close-partial-canceled", status="canceled")
            return SimpleNamespace(id="close-residual", status="accepted")

        def get_order_by_id(self, order_id):
            assert order_id == "close-partial-canceled"
            return SimpleNamespace(id=order_id, status="canceled")

    fake = FakeTradingClient()
    first.trading_client = fake
    monkeypatch.setattr(first, "_get_open_orders", lambda: [])

    first_result = first._liquidate_all_positions([])
    assert first_result[0]["order_id"] == "close-partial-canceled"
    assert first.state["emergency_liquidation_pending"] == {
        "AAPL": "close-partial-canceled"
    }
    assert submissions == ["AAPL"]

    # Simulate process restart: the terminal order ID must survive on disk.
    resumed = BrokerExecutor()
    resumed.trading_client = fake
    monkeypatch.setattr(resumed, "_get_open_orders", lambda: [])

    second_result = resumed._liquidate_all_positions([])
    assert second_result[0]["order_id"] == "close-residual"
    assert submissions == ["AAPL", "AAPL"]
    assert resumed.state["emergency_liquidation_pending"] == {
        "AAPL": "close-residual"
    }



def test_pending_close_order_lookup_timeout_survives_restart_without_duplicate(
    alpaca_env, monkeypatch
):
    """A transient order-status timeout must retain the persisted close ID."""
    from types import SimpleNamespace

    first = BrokerExecutor()
    first.state["emergency_liquidation_pending"] = {"AAPL": "close-timeout"}
    first._save_state()

    class FirstTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_order_by_id(self, order_id):
            raise TimeoutError("order status endpoint timed out")

        def close_position(self, symbol):
            pytest.fail("Must not resubmit while prior order status is unknown")

    first.trading_client = FirstTradingClient()
    monkeypatch.setattr(first, "_get_open_orders", lambda: [])
    assert first._liquidate_all_positions([]) == []
    assert first.state["emergency_liquidation_pending"] == {
        "AAPL": "close-timeout"
    }

    # Restart from disk and repeat the transient failure.
    resumed = BrokerExecutor()
    resumed_calls = []

    class ResumedTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_order_by_id(self, order_id):
            raise TimeoutError("order status endpoint still timed out")

        def close_position(self, symbol):
            resumed_calls.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    resumed.trading_client = ResumedTradingClient()
    monkeypatch.setattr(resumed, "_get_open_orders", lambda: [])
    assert resumed._liquidate_all_positions([]) == []
    assert resumed_calls == []
    assert resumed.state["emergency_liquidation_pending"] == {
        "AAPL": "close-timeout"
    }


def test_unresolved_history_timeout_preserves_intent_across_restart(
    alpaca_env, monkeypatch
):
    """Unavailable broker history is not evidence that a close was never accepted."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    first = BrokerExecutor()
    created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    first.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": created_at.isoformat(),
            "requested_qty": 1.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    class FirstTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            raise TimeoutError("broker order history timed out")

        def close_position(self, symbol):
            pytest.fail("Must not retry when order history cannot be reconciled")

    first.trading_client = FirstTradingClient()
    monkeypatch.setattr(first, "_get_open_orders", lambda: [])
    assert first._liquidate_all_positions([]) == []
    assert first.state["emergency_liquidation_unresolved"]["AAPL"]

    resumed = BrokerExecutor()
    resumed_calls = []

    class ResumedTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_orders(self, filter):
            raise TimeoutError("broker order history still timed out")

        def close_position(self, symbol):
            resumed_calls.append(symbol)
            return SimpleNamespace(id="duplicate-close", status="accepted")

    resumed.trading_client = ResumedTradingClient()
    monkeypatch.setattr(resumed, "_get_open_orders", lambda: [])
    assert resumed._liquidate_all_positions([]) == []
    assert resumed_calls == []
    assert resumed.state["emergency_liquidation_unresolved"]["AAPL"]


@pytest.mark.parametrize("status", ["done_for_day", "suspended", "replaced"])
def test_emergency_liquidation_keeps_nonterminal_or_replaced_close_order_pending(
    alpaca_env, monkeypatch, status
):
    """Uncertain close statuses must not permit a duplicate liquidation order."""
    from types import SimpleNamespace

    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-existing"}
    monkeypatch.setattr(executor, "_save_state", lambda: None)

    class FakeTradingClient:
        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="1")]

        def get_order_by_id(self, order_id):
            assert order_id == "close-existing"
            return SimpleNamespace(id=order_id, status=status)

        def close_position(self, symbol):
            pytest.fail(
                "Must not submit a duplicate close while the previous order can still execute"
            )

    executor.trading_client = FakeTradingClient()
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    orders = executor._liquidate_all_positions([])

    assert orders == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "close-existing"
    }


@pytest.mark.parametrize("status", ["done_for_day", "suspended", "replaced"])
def test_uncertain_order_status_still_reserves_remaining_exposure(
    alpaca_env, status
):
    """Potentially executable orders must not be excluded from exposure estimates."""
    from types import SimpleNamespace

    executor = BrokerExecutor()
    order = SimpleNamespace(
        status=status,
        qty="2",
        filled_qty="0",
        limit_price="10",
        symbol="AAPL",
    )

    assert executor._estimate_order_notional(order) == pytest.approx(20.0)

