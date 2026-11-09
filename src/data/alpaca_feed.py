"""AlpacaFeed – dati storici (bars) e snapshot live via alpaca-py.

Sostituisce tutti i mock presenti in trade.py e hub.py.
Compatibile con la pipeline DataProcessor esistente.
Il feed dati è definito da DATA_FEED nel market-data contract; la latenza
non è assunta come costante e dipende dal feed configurato.
"""
import logging
import os
from datetime import timedelta
from typing import List, Optional

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

try:
    from src.data.market_data_contract import (
        DATA_ADJUSTMENT, DATA_FEED, DATA_QUOTE_FEED, market_data_contract, market_data_fingerprint,
    )
    from src.data.feature_engineering import calculate_features
except ModuleNotFoundError:
    from data.market_data_contract import (
        DATA_ADJUSTMENT, DATA_FEED, DATA_QUOTE_FEED, market_data_contract, market_data_fingerprint,
    )
    from data.feature_engineering import calculate_features

_DAILY_TIMEFRAME = TimeFrame(1, TimeFrameUnit.Day)

logger = logging.getLogger(__name__)

# Monitoring-only thresholds; they never gate order flow.
QUOTE_MAX_AGE_SECONDS = float(os.getenv("STMAA_QUOTE_MAX_AGE_SECONDS", "1200"))
QUOTE_FUTURE_TOLERANCE_SECONDS = float(os.getenv("STMAA_QUOTE_FUTURE_TOLERANCE_SECONDS", "5"))
QUOTE_WIDE_SPREAD_PCT = float(os.getenv("STMAA_QUOTE_WIDE_SPREAD_PCT", "2.0"))

if QUOTE_MAX_AGE_SECONDS <= 0 or QUOTE_FUTURE_TOLERANCE_SECONDS < 0 or QUOTE_WIDE_SPREAD_PCT <= 0:
    raise ValueError("STMAA quote-quality thresholds must be positive (future tolerance may be zero)")


class AlpacaFeed:
    """Client dati di mercato reali via alpaca-py.

    Usato da PaperTradingBot.get_market_data() e da hub.py
    per prezzi live al posto dei np.random.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET", "")

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "ALPACA_API_KEY e ALPACA_API_SECRET devono essere impostati nel .env"
            )

        self.client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
        )
        logger.info("✅ AlpacaFeed inizializzato (alpaca-py)")

    # ------------------------------------------------------------------
    # Dati storici (bars giornalieri)
    # ------------------------------------------------------------------

    @staticmethod
    def _historical_request_end(now=None):
        """Return a SIP-safe end bound, excluding an in-progress daily bar."""
        current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        current = current.tz_convert("UTC")
        cutoff = current - pd.Timedelta(minutes=20)
        ny_date = current.tz_convert("America/New_York").date().isoformat()
        schedule = mcal.get_calendar("NYSE").schedule(
            start_date=ny_date, end_date=ny_date
        )
        if not schedule.empty:
            market_open = pd.Timestamp(schedule.iloc[0]["market_open"]).tz_convert("UTC")
            market_close = pd.Timestamp(schedule.iloc[0]["market_close"]).tz_convert("UTC")
            if market_open <= current < market_close:
                return pd.Timestamp(ny_date).tz_localize(
                    "America/New_York"
                ).tz_convert("UTC").to_pydatetime()
        return cutoff.to_pydatetime()

    @staticmethod
    def _latest_completed_nyse_session_date(now=None):
        """Return the most recent fully completed NYSE session date."""
        current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        current = current.tz_convert("UTC")
        calendar = mcal.get_calendar("NYSE")
        start = (current - pd.Timedelta(days=14)).date().isoformat()
        end = current.date().isoformat()
        schedule = calendar.schedule(start_date=start, end_date=end)
        if schedule.empty:
            raise RuntimeError("AlpacaFeed: NYSE calendar has no recent sessions")
        completed = schedule[schedule["market_close"] <= current]
        if completed.empty:
            raise RuntimeError("AlpacaFeed: no completed NYSE session available")
        return completed.index[-1].date()

    @classmethod
    def _validate_daily_history(cls, ticker: str, ticker_bars: pd.DataFrame, latest_session):
        """Reject incomplete or invalid daily bars across the full indicator history."""
        if ticker_bars.index.has_duplicates:
            raise RuntimeError(f"AlpacaFeed: duplicate daily bars for {ticker}")

        fields = ("Open", "High", "Low", "Close", "Volume")
        missing_fields = [field for field in fields if field not in ticker_bars.columns]
        if missing_fields:
            raise RuntimeError(
                f"AlpacaFeed: missing daily OHLCV fields for {ticker}: {', '.join(missing_fields)}"
            )
        values = ticker_bars.loc[:, fields].apply(pd.to_numeric, errors="coerce")
        invalid_rows = ~values.notna().all(axis=1)
        if invalid_rows.any():
            first_bad_index = values.index[invalid_rows][0]
            if first_bad_index == values.index[-1]:
                raise RuntimeError(
                    f"AlpacaFeed: invalid latest daily OHLCV values for {ticker}"
                )
            raise RuntimeError(
                f"AlpacaFeed: incomplete daily OHLCV bar for {ticker} at {first_bad_index.date()}"
            )
        numeric = values.to_numpy(dtype=float)
        finite_rows = pd.Series(
            np.isfinite(numeric).all(axis=1), index=values.index
        )
        if not finite_rows.all():
            first_bad_index = finite_rows.index[~finite_rows][0]
            if first_bad_index == values.index[-1]:
                raise RuntimeError(
                    f"AlpacaFeed: invalid latest daily OHLCV values for {ticker}"
                )
            raise RuntimeError(
                f"AlpacaFeed: non-finite daily OHLCV bar for {ticker} at {first_bad_index.date()}"
            )
        prices = values[["Open", "High", "Low", "Close"]]
        invalid_prices = (prices <= 0).any(axis=1)
        invalid_volume = values["Volume"] < 0
        invalid_range = (
            (values["High"] < values["Low"])
            | (values["Low"] > values["Open"])
            | (values["Low"] > values["Close"])
            | (values["High"] < values["Open"])
            | (values["High"] < values["Close"])
        )
        invalid = invalid_prices | invalid_volume | invalid_range
        if invalid.any():
            first_bad_index = invalid.index[invalid][0]
            if first_bad_index == values.index[-1]:
                raise RuntimeError(
                    f"AlpacaFeed: invalid latest daily OHLCV values for {ticker}"
                )
            raise RuntimeError(
                f"AlpacaFeed: invalid daily OHLCV values for {ticker} at {first_bad_index.date()}"
            )

        calendar = mcal.get_calendar("NYSE")
        first_date = ticker_bars.index[0].date()
        schedule = calendar.schedule(
            start_date=first_date.isoformat(), end_date=latest_session.isoformat()
        )
        expected = {stamp.date() for stamp in schedule.index}
        observed_dates = [stamp.date() for stamp in ticker_bars.index]
        observed = set(observed_dates)

        # A daily feed must contain exactly one observation per exchange session.
        # Exact timestamp duplicates are caught above; this also catches two bars
        # stamped at different times on the same session date.
        duplicate_sessions = sorted(
            session_date
            for session_date in observed
            if observed_dates.count(session_date) > 1
        )
        if duplicate_sessions:
            raise RuntimeError(
                f"AlpacaFeed: multiple daily bars for {ticker} on "
                f"{duplicate_sessions[0]}"
            )

        non_session_dates = sorted(observed - expected)
        if non_session_dates:
            raise RuntimeError(
                f"AlpacaFeed: daily bars for {ticker} fall outside NYSE sessions: "
                f"{non_session_dates[0]} ({len(non_session_dates)} non-session date(s))"
            )

        missing_sessions = sorted(expected - observed)
        if missing_sessions:
            raise RuntimeError(
                f"AlpacaFeed: missing NYSE daily bars for {ticker}: "
                f"{missing_sessions[0]} ({len(missing_sessions)} missing session(s))"
            )

    def get_historical_ohlcv(self, tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch contract-compliant adjusted daily OHLCV for model training.

        end_date is exclusive, matching the training CLI date-range contract.
        Unlike get_bars(), this method does not require the requested range to end
        at the latest session; it validates every expected NYSE session in range.
        """
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must contain unique symbols")
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        if end.tzinfo is None:
            end = end.tz_localize("UTC")
        if start >= end:
            raise ValueError("start_date must be earlier than exclusive end_date")

        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=_DAILY_TIMEFRAME,
            start=start.to_pydatetime(),
            end=end.to_pydatetime(),
            limit=None,
            feed=DATA_FEED,
            adjustment=DATA_ADJUSTMENT,
        )
        response = self.client.get_stock_bars(request)
        raw = response.df
        if raw.empty:
            raise RuntimeError("AlpacaFeed: no historical OHLCV bars returned")

        frame = raw.reset_index()
        frame.rename(columns={
            "symbol": "Symbol", "timestamp": "Date", "open": "Open",
            "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
        }, inplace=True)
        frame["Date"] = pd.to_datetime(frame["Date"], utc=True).dt.tz_localize(None)
        calendar = mcal.get_calendar("NYSE")
        expected_schedule = calendar.schedule(
            start_date=start.date().isoformat(),
            end_date=(end - pd.Timedelta(days=1)).date().isoformat(),
        )
        expected = {stamp.date() for stamp in expected_schedule.index}
        output = {}
        for ticker in tickers:
            ticker_frame = frame[frame["Symbol"] == ticker].sort_values("Date")
            if ticker_frame.empty:
                raise RuntimeError(f"AlpacaFeed: no historical bars returned for {ticker}")
            observed_dates = ticker_frame["Date"].dt.date.tolist()
            if len(observed_dates) != len(set(observed_dates)):
                raise RuntimeError(f"AlpacaFeed: duplicate historical session for {ticker}")
            observed = set(observed_dates)
            if observed != expected:
                missing = sorted(expected - observed)
                unexpected = sorted(observed - expected)
                raise RuntimeError(
                    f"AlpacaFeed: historical session mismatch for {ticker}; "
                    f"missing={missing[:3]}, unexpected={unexpected[:3]}"
                )
            values = ticker_frame.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
            values = values.apply(pd.to_numeric, errors="coerce")
            numeric = values.to_numpy(dtype=float)
            if not np.isfinite(numeric).all() or (values[["Open", "High", "Low", "Close"]] <= 0).any().any() or (values["Volume"] < 0).any():
                raise RuntimeError(f"AlpacaFeed: invalid historical OHLCV values for {ticker}")
            invalid_range = (
                (values["High"] < values["Low"])
                | (values["Low"] > values["Open"])
                | (values["Low"] > values["Close"])
                | (values["High"] < values["Open"])
                | (values["High"] < values["Close"])
            )
            if invalid_range.any():
                raise RuntimeError(f"AlpacaFeed: inconsistent historical OHLC prices for {ticker}")
            for field in values.columns:
                output[(field, ticker)] = values[field]
        result = pd.DataFrame(output)
        result.columns = pd.MultiIndex.from_tuples(result.columns, names=["field", "symbol"])
        return result.sort_index()

    @staticmethod
    def _calculate_features(frame: pd.DataFrame) -> pd.DataFrame:
        """Use the canonical versioned feature implementation for inference."""
        return calculate_features(frame)

    def get_bars(
        self,
        tickers: List[str],
        days: int = 100,
        timeframe: TimeFrame = _DAILY_TIMEFRAME,
    ) -> pd.DataFrame:
        """Scarica barre OHLCV giornaliere conformi al contratto di inferenza."""
        if timeframe != _DAILY_TIMEFRAME:
            raise ValueError(
                "AlpacaFeed inference contract requires timeframe=1Day; "
                "other timeframes require a versioned contract change and model retraining"
            )
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise ValueError("days must be a positive integer")
        if not isinstance(tickers, (list, tuple)) or not tickers:
            raise ValueError("tickers must be a non-empty sequence of unique symbols")
        if any(not isinstance(ticker, str) or not ticker.strip() for ticker in tickers):
            raise ValueError("tickers must contain non-empty symbol strings")
        if len(set(tickers)) != len(tickers):
            raise ValueError("tickers must contain unique symbols")

        end = self._historical_request_end()
        latest_completed_session = self._latest_completed_nyse_session_date(end)
        # Request enough calendar history for the requested trading sessions plus
        # the 20-observation rolling-feature warm-up, including holiday/weekend slack.
        calendar_days = int(np.ceil(days * 1.5)) + 30
        start = end - timedelta(days=calendar_days)

        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=_DAILY_TIMEFRAME,
            start=start,
            end=end,
            limit=None,
            feed=DATA_FEED,  # Versioned contract: consolidated SIP historical bars
            adjustment=DATA_ADJUSTMENT,
        )

        logger.info(f"⬇️  Scaricamento bars per {tickers} (ultimi {days}gg)…")
        try:
            bars = self.client.get_stock_bars(request).df
        except Exception as e:
            logger.error(f"Errore get_bars: {e}")
            raise

        if bars.empty:
            raise RuntimeError("AlpacaFeed: nessuna barra ricevuta da Alpaca.")

        # Multi-index (symbol, timestamp) → pivot per ticker
        bars = bars.reset_index()
        bars.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "timestamp": "Date",
                "symbol": "Symbol",
            },
            inplace=True,
        )
        bars["Date"] = pd.to_datetime(bars["Date"], utc=True).dt.tz_localize(None)
        bars = bars.set_index("Date")

        # Never silently build a partial multi-asset feature matrix: a missing
        # symbol can otherwise produce target weights from incomplete observations.
        for ticker in tickers:
            ticker_bars = bars[bars["Symbol"] == ticker].sort_index()
            if ticker_bars.empty:
                raise RuntimeError(f"AlpacaFeed: no market bars received for {ticker}")
            if timeframe == _DAILY_TIMEFRAME:
                expected_session = latest_completed_session
                latest_date = ticker_bars.index[-1].date()
                if latest_date != expected_session:
                    raise RuntimeError(
                        f"AlpacaFeed: stale daily bars for {ticker}; "
                        f"latest observation {latest_date}, expected {expected_session}"
                    )
                self._validate_daily_history(ticker, ticker_bars, expected_session)
                latest = ticker_bars.iloc[-1]
                try:
                    close = float(latest["Close"])
                    high = float(latest["High"])
                    low = float(latest["Low"])
                    volume = float(latest["Volume"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"AlpacaFeed: invalid latest daily bar for {ticker}"
                    ) from exc
                if (
                    not all(pd.notna(value) and value == value for value in (close, high, low, volume))
                    or min(close, high, low) <= 0
                    or volume < 0
                    or high < low
                    or not (low <= close <= high)
                ):
                    raise RuntimeError(
                        f"AlpacaFeed: invalid latest daily OHLCV values for {ticker}"
                    )

        # Build a shared index before adding ticker columns. This keeps all
        # symbols aligned even when their first available observation differs.
        shared_index = pd.DatetimeIndex(
            sorted(bars.index.unique()), name=bars.index.name
        )
        result = pd.DataFrame(index=shared_index)
        required_features = ("Open", "Close", "RSI", "MACD", "BB_middle", "ATR")
        feature_columns = []
        for ticker in tickers:
            sub = bars[bars["Symbol"] == ticker][["Open", "Close", "High", "Low", "Volume"]].copy()
            sub = sub.sort_index()
            result[f"Open_{ticker}"] = sub["Open"]
            result[f"Close_{ticker}"] = sub["Close"]

            indicators = self._calculate_features(sub)
            for feature in ("RSI", "MACD", "BB_middle", "ATR"):
                result[f"{feature}_{ticker}"] = indicators[feature].reindex(result.index)
            feature_columns.extend(f"{feature}_{ticker}" for feature in required_features)

        numeric_features = result.loc[:, feature_columns].apply(
            pd.to_numeric, errors="coerce"
        )
        complete_rows = np.isfinite(numeric_features.to_numpy(dtype=float)).all(axis=1)
        if not complete_rows.any():
            raise RuntimeError(
                "AlpacaFeed: insufficient history to produce complete strategy features"
            )

        first_complete = int(np.flatnonzero(complete_rows)[0])
        if not complete_rows[first_complete:].all():
            raise RuntimeError(
                "AlpacaFeed: non-finite strategy features found after indicator warm-up"
            )

        # Discard only the leading indicator warm-up rows, then retain the
        # requested number of complete, synchronized observations.
        result = result.loc[complete_rows].tail(days).copy()
        if len(result) < days:
            raise RuntimeError(
                "AlpacaFeed: insufficient complete strategy history; "
                f"received {len(result)} rows, required {days}"
            )

        # Carry the exact feature/provider contract with the final inference frame.
        # Fingerprinting after warm-up filtering binds the metadata to the exact
        # finite frame that PPO will consume.
        result.attrs["market_data_contract"] = market_data_contract()
        result.attrs["market_data_fingerprint"] = market_data_fingerprint(result)
        logger.info(f"Bars pronte: {len(result)} righe, {len(tickers)} tickers")
        return result

    # ------------------------------------------------------------------
    # Snapshot live (prezzi correnti per hub.py)
    # ------------------------------------------------------------------

    def get_latest_quotes(self, tickers: List[str], now=None) -> dict:
        """Return snapshots with monitoring-only quote-quality telemetry.

        Age uses a 20-minute default to accommodate delayed SIP. Spread and
        timestamp anomalies are reported, not used to alter prices or gate trades.
        The optional now argument exists for deterministic tests.
        """
        request = StockLatestQuoteRequest(symbol_or_symbols=tickers, feed=DATA_QUOTE_FEED)
        try:
            quotes = self.client.get_stock_latest_quote(request)
        except Exception as e:
            logger.error(f"Errore get_latest_quotes: {e}")
            return {t: {"price": 0.0, "change_pct": 0.0, "timestamp": None,
                        "quote_age_seconds": None, "spread": None, "spread_pct": None,
                        "quality_status": "unavailable", "quality_flags": ["unavailable"]}
                    for t in tickers}

        result = {}
        for ticker in tickers:
            q = quotes.get(ticker)
            if q is None:
                result[ticker] = {
                    "price": 0.0, "change_pct": 0.0, "timestamp": None,
                    "quote_age_seconds": None, "spread": None, "spread_pct": None,
                    "quality_status": "missing", "quality_flags": ["missing"],
                }
                continue
            try:
                bid = float(q.bid_price)
                ask = float(q.ask_price)
                quote_time = getattr(q, "timestamp", None)
                if (
                    not np.isfinite(bid)
                    or not np.isfinite(ask)
                    or bid <= 0
                    or ask <= 0
                    or ask < bid
                    or quote_time is None
                ):
                    raise ValueError("quote has invalid bid/ask or missing timestamp")
                parsed_timestamp = pd.Timestamp(quote_time)
                if parsed_timestamp.tzinfo is None:
                    raise ValueError("quote timestamp must be timezone-aware")
                parsed_timestamp = parsed_timestamp.tz_convert("UTC")
                current_time = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
                if current_time.tzinfo is None:
                    raise ValueError("validation time must be timezone-aware")
                current_time = current_time.tz_convert("UTC")
                age_seconds = (current_time - parsed_timestamp).total_seconds()
                mid = (ask + bid) / 2
                spread = ask - bid
                spread_pct = (spread / mid) * 100
                flags = []
                if age_seconds > QUOTE_MAX_AGE_SECONDS:
                    flags.append("stale")
                if age_seconds < -QUOTE_FUTURE_TOLERANCE_SECONDS:
                    flags.append("future_timestamp")
                elif age_seconds < 0:
                    flags.append("clock_skew")
                if spread_pct > QUOTE_WIDE_SPREAD_PCT:
                    flags.append("wide_spread")
                status = next(
                    (
                        candidate
                        for candidate in (
                            "future_timestamp", "stale", "wide_spread", "clock_skew"
                        )
                        if candidate in flags
                    ),
                    "ok",
                )
                timestamp = parsed_timestamp.isoformat()
                result[ticker] = {
                    "price": round(mid, 6),
                    "change_pct": 0.0,
                    "timestamp": timestamp,
                    "quote_age_seconds": round(age_seconds, 3),
                    "spread": round(spread, 6),
                    "spread_pct": round(spread_pct, 4),
                    "quality_status": status,
                    "quality_flags": flags,
                }
            except (TypeError, ValueError, OverflowError) as exc:
                logger.warning("Invalid latest quote for %s: %s", ticker, exc)
                result[ticker] = {
                    "price": 0.0, "change_pct": 0.0, "timestamp": None,
                    "quote_age_seconds": None, "spread": None, "spread_pct": None,
                    "quality_status": "invalid", "quality_flags": ["invalid"],
                }

        # Tenta di arricchire con variazione % usando barre intraday
        try:
            prev = self._get_prev_close(
                tickers,
                quote_timestamps={
                    ticker: result[ticker]["timestamp"] for ticker in tickers
                },
            )
            for ticker in tickers:
                if ticker in prev and prev[ticker] > 0:
                    cur = result[ticker]["price"]
                    result[ticker]["change_pct"] = round(
                        (cur - prev[ticker]) / prev[ticker] * 100, 2
                    )
        except Exception as e:
            logger.warning(f"change_pct non disponibile: {e}")

        return result

    def _get_prev_close(self, tickers: List[str], quote_timestamps=None) -> dict:
        end = self._historical_request_end()
        start = end - timedelta(days=5)
        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            start=start,
            end=end,
            feed=DATA_FEED,
            adjustment=DATA_ADJUSTMENT,
        )
        try:
            bars = self.client.get_stock_bars(request).df.reset_index()
        except Exception:
            return {}
        bars.rename(columns={"symbol": "Symbol", "close": "Close"}, inplace=True)
        result = {}
        quote_timestamps = quote_timestamps or {}
        for ticker in tickers:
            ticker_bars = bars[bars["Symbol"] == ticker]
            sub = ticker_bars["Close"]
            if not sub.empty:
                last_bar_date = pd.to_datetime(
                    ticker_bars["timestamp"].iloc[-1], utc=True
                ).tz_convert("America/New_York").date()
                quote_timestamp = quote_timestamps.get(ticker)
                quote_date = (
                    pd.Timestamp(quote_timestamp).tz_convert("America/New_York").date()
                    if quote_timestamp is not None
                    else None
                )
                # Compare the delayed quote's session with the latest returned
                # daily bar. If they match, that bar is today's close and the
                # prior close is the penultimate bar; otherwise use the latest
                # bar as the previous close.
                position = -2 if quote_date == last_bar_date else -1
                if len(sub) >= abs(position):
                    result[ticker] = float(sub.iloc[position])
        return result
