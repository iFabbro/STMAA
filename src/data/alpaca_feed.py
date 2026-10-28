"""AlpacaFeed – dati storici (bars) e snapshot live via alpaca-py.

Sostituisce tutti i mock presenti in trade.py e hub.py.
Compatibile con la pipeline DataProcessor esistente.
Usa endpoint free-tier (15min delay).
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd
import pandas_market_calendars as mcal

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

_DAILY_TIMEFRAME = TimeFrame(1, TimeFrameUnit.Day)

logger = logging.getLogger(__name__)


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

    def get_bars(
        self,
        tickers: List[str],
        days: int = 100,
        timeframe: TimeFrame = _DAILY_TIMEFRAME,
    ) -> pd.DataFrame:
        """Scarica barre OHLCV giornaliere per `days` giorni.

        Usa adjust='all' per bypassare limiti SIP free-tier.
        
        Returns
        -------
        DataFrame con colonne flat: Close_AAPL, RSI_AAPL, MACD_AAPL, …
        Compatibile con generate_weights() in trade.py.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 60)  # buffer per indicatori

        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=None,
            feed='iex',  # Free tier: IEX data (15min delay)
            adjustment='all',
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
                expected_session = self._latest_completed_nyse_session_date(end)
                latest_date = ticker_bars.index[-1].date()
                if latest_date != expected_session:
                    raise RuntimeError(
                        f"AlpacaFeed: stale daily bars for {ticker}; "
                        f"latest observation {latest_date}, expected {expected_session}"
                    )
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

        # Costruisce DataFrame flat atteso dalla pipeline
        result = pd.DataFrame()
        for ticker in tickers:
            sub = bars[bars["Symbol"] == ticker][["Close", "High", "Low", "Volume"]].copy()
            sub = sub.sort_index().tail(days)

            result[f"Close_{ticker}"] = sub["Close"]

            # RSI 14
            delta = sub["Close"].diff()
            gain = delta.where(delta > 0, 0.0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rs = gain / loss
            result[f"RSI_{ticker}"] = (100 - 100 / (1 + rs)).reindex(result.index)

            # MACD
            exp1 = sub["Close"].ewm(span=12, adjust=False).mean()
            exp2 = sub["Close"].ewm(span=26, adjust=False).mean()
            result[f"MACD_{ticker}"] = (exp1 - exp2).reindex(result.index)

            # BB middle (SMA 20)
            result[f"BB_middle_{ticker}"] = (
                sub["Close"].rolling(20).mean().reindex(result.index)
            )

            # ATR
            tr = pd.concat(
                [
                    sub["High"] - sub["Low"],
                    (sub["High"] - sub["Close"].shift(1)).abs(),
                    (sub["Low"] - sub["Close"].shift(1)).abs(),
                ],
                axis=1,
            ).max(axis=1)
            result[f"ATR_{ticker}"] = tr.rolling(14).mean().reindex(result.index)

        result.dropna(how="all", inplace=True)
        logger.info(f"✅ Bars pronte: {len(result)} righe, {len(tickers)} tickers")
        return result

    # ------------------------------------------------------------------
    # Snapshot live (prezzi correnti per hub.py)
    # ------------------------------------------------------------------

    def get_latest_quotes(self, tickers: List[str]) -> dict:
        """Ritorna dict {ticker: {'price': float, 'change_pct': float}}.

        Usa feed='iex' per free-tier.
        """
        request = StockLatestQuoteRequest(symbol_or_symbols=tickers, feed='iex')
        try:
            quotes = self.client.get_stock_latest_quote(request)
        except Exception as e:
            logger.error(f"Errore get_latest_quotes: {e}")
            return {t: {"price": 0.0, "change_pct": 0.0, "timestamp": None} for t in tickers}

        result = {}
        for ticker in tickers:
            q = quotes.get(ticker)
            if q is None:
                result[ticker] = {"price": 0.0, "change_pct": 0.0, "timestamp": None}
                continue
            try:
                bid = float(q.bid_price)
                ask = float(q.ask_price)
                quote_time = getattr(q, "timestamp", None)
                if (
                    not pd.notna(bid)
                    or not pd.notna(ask)
                    or bid <= 0
                    or ask <= 0
                    or ask < bid
                    or quote_time is None
                ):
                    raise ValueError("quote has invalid bid/ask or missing timestamp")
                timestamp = (
                    quote_time.isoformat()
                    if hasattr(quote_time, "isoformat")
                    else str(quote_time)
                )
                mid = (ask + bid) / 2
                result[ticker] = {
                    "price": round(mid, 6),
                    "change_pct": 0.0,
                    "timestamp": timestamp,
                }
            except (TypeError, ValueError, OverflowError) as exc:
                logger.warning("Invalid latest quote for %s: %s", ticker, exc)
                result[ticker] = {"price": 0.0, "change_pct": 0.0, "timestamp": None}

        # Tenta di arricchire con variazione % usando barre intraday
        try:
            prev = self._get_prev_close(tickers)
            for ticker in tickers:
                if ticker in prev and prev[ticker] > 0:
                    cur = result[ticker]["price"]
                    result[ticker]["change_pct"] = round(
                        (cur - prev[ticker]) / prev[ticker] * 100, 2
                    )
        except Exception as e:
            logger.warning(f"change_pct non disponibile: {e}")

        return result

    def _get_prev_close(self, tickers: List[str]) -> dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=5)
        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            start=start,
            end=end,
            feed='iex',
            adjustment='all',
        )
        try:
            bars = self.client.get_stock_bars(request).df.reset_index()
        except Exception:
            return {}
        bars.rename(columns={"symbol": "Symbol", "close": "Close"}, inplace=True)
        result = {}
        for ticker in tickers:
            sub = bars[bars["Symbol"] == ticker]["Close"]
            if len(sub) >= 2:
                result[ticker] = float(sub.iloc[-2])
        return result
