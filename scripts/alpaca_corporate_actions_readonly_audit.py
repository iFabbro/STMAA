#!/usr/bin/env python3
"""Read-only audit of Alpaca's real corporate-action bar adjustments.

This script uses only the Market Data API. It never accesses the Trading API
and never submits, replaces, or cancels orders.
"""
from __future__ import annotations

import argparse
import json
import os
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from src.data.market_data_contract import DATA_ADJUSTMENT, DATA_FEED

CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
EVENT_BUCKETS = {
    "split": ("forward_splits", "reverse_splits"),
    "dividend": ("cash_dividends",),
    "spin_off": ("spin_offs",),
}
EVENT_TYPES = "forward_split,reverse_split,cash_dividend,spin_off"
OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MAX_CANDIDATES_PER_TYPE = 25
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_MAX_DELAY_SECONDS = 30


class NoBarsAvailable(RuntimeError):
    """The selected feed returned no bars for this symbol/date window."""


def _is_rate_limited(exc: Exception) -> bool:
    """Recognize HTTP 429 from urllib and Alpaca SDK exceptions."""
    if isinstance(exc, HTTPError) and exc.code == 429:
        return True
    return getattr(exc, "status_code", None) == 429


def _call_with_rate_limit_retry(operation, context: str):
    """Retry transient Alpaca rate limits without masking permanent failures."""
    for attempt in range(RATE_LIMIT_MAX_ATTEMPTS):
        try:
            return operation()
        except Exception as exc:
            if not _is_rate_limited(exc):
                raise
            if attempt == RATE_LIMIT_MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    f"Alpaca rate limit persisted after {RATE_LIMIT_MAX_ATTEMPTS} "
                    f"attempts while {context}"
                ) from exc

            headers = getattr(exc, "headers", None)
            retry_after = headers.get("Retry-After") if headers else None
            try:
                delay = float(retry_after) if retry_after is not None else 2 ** attempt
            except (TypeError, ValueError):
                delay = 2 ** attempt
            delay = min(max(delay, 0.0), RATE_LIMIT_MAX_DELAY_SECONDS)
            time_module.sleep(delay)


def _get_json(url: str, params: dict[str, str], api_key: str, api_secret: str) -> dict[str, Any]:
    request_url = f"{url}?{urlencode(params)}"
    request = Request(
        request_url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        },
    )
    def request_json() -> dict[str, Any]:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    return _call_with_rate_limit_retry(
        request_json, "fetching corporate-action records"
    )


def fetch_corporate_actions(
    api_key: str, api_secret: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Fetch every page of complete split, cash-dividend, and spin-off records."""
    records: list[dict[str, Any]] = []
    page_token = None
    while True:
        params = {
            "types": EVENT_TYPES,
            "start": start_date,
            "end": end_date,
            "limit": "1000",
            "sort": "desc",
            "data_quality": "complete",
        }
        if page_token:
            params["page_token"] = page_token
        payload = _get_json(CORPORATE_ACTIONS_URL, params, api_key, api_secret)
        grouped = payload.get("corporate_actions")
        if not isinstance(grouped, dict):
            raise RuntimeError("Alpaca corporate-actions response has no corporate_actions object")
        for event_type, buckets in EVENT_BUCKETS.items():
            for bucket in buckets:
                events = grouped.get(bucket, [])
                if not isinstance(events, list):
                    raise RuntimeError(f"Alpaca corporate-actions bucket {bucket} is malformed")
                for event in events:
                    if isinstance(event, dict):
                        records.append({**event, "_audit_type": event_type})
        page_token = payload.get("next_page_token")
        if not page_token:
            return records


def _event_symbol(event: dict[str, Any]) -> str | None:
    return event.get("source_symbol") if event.get("_audit_type") == "spin_off" else event.get("symbol")


def select_latest_event_per_type(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return newest independently auditable events, retaining fallback candidates.

    An event is eligible only if it is the latest known action for its source
    symbol in the fetched corporate-action set. Multiple symbols are retained
    so an OTC/uncovered symbol does not prevent auditing that event class.
    """
    latest_by_symbol: dict[str, date] = {}
    for event in events:
        symbol = _event_symbol(event)
        ex_date = event.get("ex_date")
        if not symbol or not ex_date:
            continue
        parsed = date.fromisoformat(ex_date)
        latest_by_symbol[symbol] = max(parsed, latest_by_symbol.get(symbol, date.min))

    selected: dict[str, list[dict[str, Any]]] = {}
    for event_type in EVENT_BUCKETS:
        candidates = [
            event for event in events
            if event.get("_audit_type") == event_type
            and _event_symbol(event)
            and event.get("ex_date")
            and date.fromisoformat(event["ex_date"]) == latest_by_symbol.get(_event_symbol(event))
        ]
        candidates.sort(key=lambda item: item["ex_date"], reverse=True)
        if candidates:
            selected[event_type] = candidates[:MAX_CANDIDATES_PER_TYPE]
    return selected


def fetch_daily_bars(
    client: StockHistoricalDataClient,
    symbol: str,
    ex_date: date,
    adjustment: str,
) -> pd.DataFrame:
    start = datetime.combine(ex_date - timedelta(days=12), time.min, tzinfo=timezone.utc)
    end = datetime.combine(ex_date + timedelta(days=4), time.max, tzinfo=timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=start,
        end=end,
        limit=10000,
        feed=DATA_FEED,
        adjustment=adjustment,
    )
    response = _call_with_rate_limit_retry(
        lambda: client.get_stock_bars(request),
        f"fetching {adjustment} daily bars for {symbol} around {ex_date}",
    )
    if response.df.empty:
        raise NoBarsAvailable(f"No {adjustment} daily bars returned for {symbol} around {ex_date}")
    frame = response.df.reset_index()
    if "symbol" in frame.columns:
        frame = frame[frame["symbol"] == symbol]
    if frame.empty:
        raise NoBarsAvailable(f"No {adjustment} daily bars returned for {symbol} around {ex_date}")
    if "timestamp" not in frame.columns:
        raise RuntimeError(f"Alpaca {adjustment} response has no timestamp column for {symbol}")
    frame["session"] = pd.to_datetime(frame["timestamp"], utc=True).dt.date
    frame = frame.set_index("session").sort_index()
    missing = [field for field in OHLCV_FIELDS if field not in frame.columns]
    if missing:
        raise RuntimeError(f"Alpaca {adjustment} response missing fields for {symbol}: {missing}")
    return frame.loc[:, OHLCV_FIELDS].apply(pd.to_numeric, errors="coerce")


def validate_adjustment_effect(
    raw: pd.DataFrame,
    adjusted: pd.DataFrame,
    event_type: str,
    ex_date: date,
) -> dict[str, Any]:
    """Check that adjustment=all changes only the expected historical dimensions."""
    if raw.empty or adjusted.empty or not raw.index.equals(adjusted.index):
        raise AssertionError("raw and adjusted bars must have identical non-empty sessions")
    if raw.isna().any().any() or adjusted.isna().any().any():
        raise AssertionError("raw or adjusted OHLCV contains missing/non-numeric values")
    if ex_date not in raw.index:
        raise AssertionError(f"ex-date {ex_date} is absent from returned daily bars")
    pre_event = raw.index < ex_date
    if not pre_event.any():
        raise AssertionError(f"No pre-event daily bars returned for {ex_date}")

    price_changed = not np.allclose(
        raw.loc[pre_event, "close"].to_numpy(),
        adjusted.loc[pre_event, "close"].to_numpy(),
        rtol=1e-7,
        atol=1e-8,
    )
    volume_changed = not np.allclose(
        raw.loc[pre_event, "volume"].to_numpy(),
        adjusted.loc[pre_event, "volume"].to_numpy(),
        rtol=1e-7,
        atol=1e-8,
    )
    if not price_changed:
        raise AssertionError(f"adjustment=all did not adjust pre-event prices for {event_type}")
    if event_type == "split" and not volume_changed:
        raise AssertionError("adjustment=all did not adjust pre-event volume for split")
    if event_type in ("dividend", "spin_off") and volume_changed:
        raise AssertionError(f"{event_type} unexpectedly changed historical volume")

    post_event = raw.index >= ex_date
    if post_event.any() and not np.allclose(
        raw.loc[post_event, list(OHLCV_FIELDS)].to_numpy(),
        adjusted.loc[post_event, list(OHLCV_FIELDS)].to_numpy(),
        rtol=1e-7,
        atol=1e-8,
    ):
        raise AssertionError(
            "adjustment=all changed bars on/after the event; another later event may overlap"
        )
    return {
        "event_type": event_type,
        "ex_date": ex_date.isoformat(),
        "pre_event_sessions": int(pre_event.sum()),
        "price_adjustment_observed": price_changed,
        "volume_adjustment_observed": volume_changed,
        "post_event_bars_unchanged": bool(post_event.any()),
    }


def run_audit(
    api_key: str,
    api_secret: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    events = fetch_corporate_actions(api_key, api_secret, start_date, end_date)
    candidates_by_type = select_latest_event_per_type(events)
    missing_types = sorted(set(EVENT_BUCKETS) - set(candidates_by_type))
    if missing_types:
        raise RuntimeError(
            "INCONCLUSIVE: no latest complete Alpaca event available for "
            + ", ".join(missing_types)
            + f" in {start_date}..{end_date}; widen the date range"
        )

    client = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
    results = []
    skipped_candidates: dict[str, list[dict[str, str]]] = {}
    for event_type, candidates in candidates_by_type.items():
        result = None
        for event in candidates:
            symbol = _event_symbol(event)
            ex_date = date.fromisoformat(event["ex_date"])
            try:
                raw = fetch_daily_bars(client, symbol, ex_date, "raw")
            except NoBarsAvailable as exc:
                skipped_candidates.setdefault(event_type, []).append({
                    "symbol": symbol,
                    "ex_date": ex_date.isoformat(),
                    "reason": str(exc),
                })
                continue

            # Some valid corporate actions have no IEX trade/bar on the exact
            # ex-date (for example, illiquid symbols). Without that session the
            # event boundary cannot be validated reliably. Record it as
            # unverifiable and try another independent candidate; never PASS it.
            if ex_date not in raw.index:
                skipped_candidates.setdefault(event_type, []).append({
                    "symbol": symbol,
                    "ex_date": ex_date.isoformat(),
                    "reason": (
                        f"No raw daily bar on ex-date {ex_date}; "
                        "event boundary cannot be verified on this feed"
                    ),
                })
                continue

            # Once the raw event session exists, missing adjusted data or a
            # quantitative mismatch remains a hard failure, not a skip.
            adjusted = fetch_daily_bars(client, symbol, ex_date, DATA_ADJUSTMENT)
            result = validate_adjustment_effect(raw, adjusted, event_type, ex_date)
            result.update({
                "symbol": symbol,
                "corporate_action_id": event.get("id"),
                "process_date": event.get("process_date"),
                "feed": DATA_FEED,
                "adjustment": DATA_ADJUSTMENT,
            })
            break

        if result is None:
            skipped = skipped_candidates.get(event_type, [])
            raise RuntimeError(
                f"INCONCLUSIVE: no {event_type} candidate has auditable raw ex-date coverage "
                f"on feed {DATA_FEED}; checked {len(skipped)} candidate(s): "
                + json.dumps(skipped, sort_keys=True)
            )
        results.append(result)

    if skipped_candidates:
        for result in results:
            result["skipped_uncovered_candidates"] = skipped_candidates.get(
                result["event_type"], []
            )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    if not api_key or not api_secret:
        parser.error("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    if date.fromisoformat(args.start_date) > date.fromisoformat(args.end_date):
        parser.error("--start-date must not be after --end-date")
    try:
        results = run_audit(api_key, api_secret, args.start_date, args.end_date)
    except Exception as exc:
        print(json.dumps({"status": "FAIL_OR_INCONCLUSIVE", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"status": "PASS", "events": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
