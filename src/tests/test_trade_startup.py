import trade


def test_preflight_only_passes_validated_config_and_never_starts_trading_loop(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key-example")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret-example")
    monkeypatch.setenv("MAX_PORTFOLIO_EXPOSURE", "0.42")
    monkeypatch.setenv("MAX_DRAWDOWN", "0.12")
    monkeypatch.setenv("LIQUIDATE_ON_DRAWDOWN", "true")

    observed = {}

    class FakeExecutor:
        paper_trading = True

        def get_account_status(self):
            return {
                "account_data_fresh": True,
                "account_id": "paper-account-123",
                "equity": 10000.0,
                "cash": 9000.0,
                "buying_power": 18000.0,
            }

        def execute_weights(self, *_args, **_kwargs):
            raise AssertionError("preflight-only must never submit target weights")

    class FakeBot:
        def __init__(self, **kwargs):
            observed["kwargs"] = kwargs
            self.executor = FakeExecutor()

        def run_loop(self, **_kwargs):
            raise AssertionError("preflight-only must not start the trading loop")

    monkeypatch.setattr(trade, "PaperTradingBot", FakeBot)

    assert trade.main(["--preflight-only"]) == 0
    assert observed["kwargs"]["max_drawdown_threshold"] == 0.12
    assert observed["kwargs"]["max_gross_exposure"] == 0.42
    assert observed["kwargs"]["liquidate_on_drawdown"] is True
    assert observed["kwargs"]["api_key"] == "paper-key-example"
    assert observed["kwargs"]["api_secret"] == "paper-secret-example"
    assert observed["kwargs"]["base_url"] == "https://paper-api.alpaca.markets"


def test_invalid_startup_config_stops_before_bot_construction(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key-example")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret-example")
    monkeypatch.setenv("STMAA_TRADING_MODE", "live")

    class ForbiddenBot:
        def __init__(self, **_kwargs):
            raise AssertionError("invalid config must be rejected before bot construction")

    monkeypatch.setattr(trade, "PaperTradingBot", ForbiddenBot)

    assert trade.main(["--preflight-only"]) == 1
