"""Regression tests for Alpaca inference feature warm-up and alignment."""

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytest

from src.agents.ppo_agent import PPOAgent
from src.data.alpaca_feed import AlpacaFeed
from src.data.market_data_contract import (
    market_data_contract,
    market_data_fingerprint,
)


class _BarsResponse:
    def __init__(self, frame):
        self.df = frame


class _FakeHistoricalClient:
    def __init__(self, frame):
        self.frame = frame

    def get_stock_bars(self, _request):
        return _BarsResponse(self.frame.copy())


def _alpaca_bars(tickers=("AAA", "BBB"), sessions=55, stagger_second_ticker=0):
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date="2026-07-01", end_date="2026-11-03"
    )
    dates = schedule.index[-sessions:]
    records = []
    for ticker_index, ticker in enumerate(tickers):
        ticker_dates = dates[stagger_second_ticker:] if ticker_index == 1 else dates
        for index, session in enumerate(ticker_dates):
            close = 80.0 + ticker_index * 25.0 + index * 0.31 + np.sin(index / 2.7)
            records.append(
                {
                    "symbol": ticker,
                    "timestamp": pd.Timestamp(session).tz_localize("UTC"),
                    "open": close - 0.2,
                    "high": close + 0.8,
                    "low": close - 0.9,
                    "close": close,
                    "volume": 10000.0 + index,
                }
            )
    return pd.DataFrame(records).set_index(["symbol", "timestamp"]).sort_index()


def _feed_for_bars(monkeypatch, raw_bars):
    end = pd.Timestamp("2026-11-04T00:00:00Z")
    latest_session = pd.Timestamp("2026-11-03").date()
    monkeypatch.setattr(
        AlpacaFeed, "_historical_request_end", staticmethod(lambda: end)
    )
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda _now=None: latest_session),
    )
    monkeypatch.setattr(
        AlpacaFeed, "_validate_daily_history", classmethod(lambda *_args: None)
    )
    monkeypatch.setattr(
        "src.data.alpaca_feed.StockBarsRequest",
        lambda **kwargs: kwargs,
    )
    feed = AlpacaFeed.__new__(AlpacaFeed)
    feed.client = _FakeHistoricalClient(raw_bars)
    return feed


def test_get_bars_returns_requested_count_after_indicator_warmup(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(sessions=55))

    result = feed.get_bars(["AAA"], days=25)

    assert len(result) == 25
    assert np.isfinite(result.to_numpy(dtype=float)).all()
    assert result.index.is_monotonic_increasing
    assert result.index[-1].date() == pd.Timestamp("2026-11-03").date()


def test_get_bars_excludes_leading_rows_with_incomplete_indicators(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(tickers=("AAA",), sessions=40))

    result = feed.get_bars(["AAA"], days=15)

    assert len(result) == 15
    assert np.isfinite(result[["RSI_AAA", "MACD_AAA", "BB_middle_AAA", "ATR_AAA"]]).all().all()


def test_get_bars_fails_when_complete_history_is_insufficient(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(tickers=("AAA",), sessions=15))

    with pytest.raises(RuntimeError, match="insufficient history|insufficient complete"):
        feed.get_bars(["AAA"], days=10)


def test_get_bars_aligns_warmup_across_multiple_tickers(monkeypatch):
    raw = _alpaca_bars(tickers=("AAA", "BBB"), sessions=50, stagger_second_ticker=4)
    feed = _feed_for_bars(monkeypatch, raw)

    result = feed.get_bars(["AAA", "BBB"], days=20)

    assert len(result) == 20
    assert result.index.is_unique
    assert np.isfinite(result.to_numpy(dtype=float)).all()
    assert list(result.columns) == [
        "Open_AAA", "Close_AAA", "RSI_AAA", "MACD_AAA", "BB_middle_AAA", "ATR_AAA",
        "Open_BBB", "Close_BBB", "RSI_BBB", "MACD_BBB", "BB_middle_BBB", "ATR_BBB",
    ]


def test_get_bars_rejects_non_finite_features_after_warmup(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(tickers=("AAA",), sessions=45))
    original = feed._calculate_features

    def inject_interior_nan(frame):
        features = original(frame)
        features.iloc[-3, features.columns.get_loc("BB_middle")] = np.nan
        return features

    monkeypatch.setattr(feed, "_calculate_features", inject_interior_nan)

    with pytest.raises(RuntimeError, match="non-finite strategy features"):
        feed.get_bars(["AAA"], days=15)


def test_get_bars_computes_fingerprint_after_warmup_filtering(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(tickers=("AAA",), sessions=45))

    result = feed.get_bars(["AAA"], days=15)

    assert result.attrs["market_data_contract"] == market_data_contract()
    assert result.attrs["market_data_fingerprint"] == market_data_fingerprint(result)


def test_get_bars_preserves_latest_completed_session(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(tickers=("AAA",), sessions=45))

    result = feed.get_bars(["AAA"], days=15)

    assert result.index[-1].date() == pd.Timestamp("2026-11-03").date()


def test_get_bars_output_is_accepted_by_ppo_inference(monkeypatch):
    feed = _feed_for_bars(monkeypatch, _alpaca_bars(tickers=("AAA",), sessions=55))
    market_frame = feed.get_bars(["AAA"], days=25)
    agent = PPOAgent(tickers=["AAA"], load_existing_model=False)

    class CapturingModel:
        def __init__(self):
            self.called = False

        def predict(self, observation, deterministic=True):
            self.called = True
            assert np.isfinite(observation).all()
            return np.array([1.0]), None

    model = CapturingModel()
    agent.model = model

    weights = agent.predict_weights(market_frame)

    assert model.called
    np.testing.assert_allclose(weights, np.array([agent.max_position_size]))


def test_get_bars_does_not_change_feature_contract():
    contract = market_data_contract()

    assert contract["features"] == ["Close", "RSI", "MACD", "BB_middle", "ATR"]
    assert contract["feature_parameters"]["bb_period"] == 20
    assert contract["semantics"]["indicator_warmup"] == (
        "full_rolling_window; leading_nan_rows_dropped_before_training"
    )
