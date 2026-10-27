


def test_stale_quote_for_one_symbol_blocks_entire_multi_symbol_rebalance(
    alpaca_env, monkeypatch
):
    """A valid quote for one symbol must not permit a partial rebalance."""
    executor = BrokerExecutor()
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            "AAPL": {
                "price": 100.0,
                "timestamp": (now - timedelta(seconds=10)).isoformat(),
            },
            "MSFT": {
                "price": 200.0,
                "timestamp": (now - timedelta(minutes=10)).isoformat(),
            },
        },
    )
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="valid prices unavailable for: MSFT",
    ):
        executor.execute_weights(
            PortfolioWeights(
                weights=np.array([0.1, 0.1]),
                tickers=["AAPL", "MSFT"],
            )
        )

    assert submitted == []
