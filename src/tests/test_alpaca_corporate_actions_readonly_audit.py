from datetime import date
from io import BytesIO
from urllib.error import HTTPError

import pandas as pd
import pytest

from scripts import alpaca_corporate_actions_readonly_audit as audit


def _bars():
    index = pd.Index(
        [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)],
        name="session",
    )
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000.0, 1100.0, 1200.0],
        },
        index=index,
    )


@pytest.mark.parametrize(
    ("event_type", "price_factor", "volume_factor"),
    [
        ("split", 0.5, 2.0),
        ("dividend", 0.99, 1.0),
        ("spin_off", 0.98, 1.0),
    ],
)
def test_real_provider_adjustment_dimensions_are_checked(
    event_type, price_factor, volume_factor
):
    raw = _bars()
    adjusted = raw.copy()
    pre_event = adjusted.index < date(2025, 1, 3)
    adjusted.loc[pre_event, ["open", "high", "low", "close"]] *= price_factor
    adjusted.loc[pre_event, "volume"] *= volume_factor

    result = audit.validate_adjustment_effect(
        raw, adjusted, event_type, date(2025, 1, 3)
    )

    assert result["event_type"] == event_type
    assert result["price_adjustment_observed"] is True
    assert result["volume_adjustment_observed"] is (event_type == "split")
    assert result["post_event_bars_unchanged"] is True


def test_adjustment_audit_rejects_unadjusted_provider_response():
    raw = _bars()

    with pytest.raises(AssertionError, match="did not adjust pre-event prices"):
        audit.validate_adjustment_effect(raw, raw.copy(), "dividend", date(2025, 1, 3))


def test_adjustment_audit_rejects_split_without_volume_adjustment():
    raw = _bars()
    adjusted = raw.copy()
    pre_event = adjusted.index < date(2025, 1, 3)
    adjusted.loc[pre_event, ["open", "high", "low", "close"]] *= 0.5

    with pytest.raises(AssertionError, match="did not adjust pre-event volume"):
        audit.validate_adjustment_effect(raw, adjusted, "split", date(2025, 1, 3))


def test_adjustment_audit_rejects_missing_ex_date():
    raw = _bars().drop(index=date(2025, 1, 3))

    with pytest.raises(AssertionError, match="ex-date"):
        audit.validate_adjustment_effect(raw, raw.copy(), "dividend", date(2025, 1, 3))


def test_event_selection_keeps_latest_independently_auditable_candidates():
    events = [
        {"_audit_type": "split", "symbol": "AAA", "ex_date": "2024-01-10"},
        {"_audit_type": "dividend", "symbol": "AAA", "ex_date": "2024-02-10"},
        {"_audit_type": "dividend", "symbol": "BBB", "ex_date": "2024-03-10"},
        {"_audit_type": "spin_off", "source_symbol": "CCC", "ex_date": "2024-04-10"},
    ]

    selected = audit.select_latest_event_per_type(events)

    # AAA's split is not eligible because a later action exists for AAA.
    assert "split" not in selected
    assert [event["symbol"] for event in selected["dividend"]] == ["BBB", "AAA"]
    assert selected["spin_off"][0]["source_symbol"] == "CCC"


def test_run_audit_skips_uncovered_symbol_and_uses_next_candidate(monkeypatch):
    events = [
        {"_audit_type": "split", "symbol": "CLGCF", "ex_date": "2025-01-10", "id": "otc"},
        {"_audit_type": "split", "symbol": "AAA", "ex_date": "2025-01-03", "id": "covered"},
        {"_audit_type": "dividend", "symbol": "BBB", "ex_date": "2024-03-10", "id": "div"},
        {"_audit_type": "spin_off", "source_symbol": "CCC", "ex_date": "2025-01-03", "id": "spin"},
    ]
    monkeypatch.setattr(audit, "fetch_corporate_actions", lambda *args: events)
    monkeypatch.setattr(audit, "StockHistoricalDataClient", lambda **kwargs: object())

    def fake_bars(_client, symbol, ex_date, adjustment):
        if symbol == "CLGCF":
            raise audit.NoBarsAvailable("no IEX raw bars for CLGCF")
        raw = _bars()
        if adjustment == "raw":
            return raw
        adjusted = raw.copy()
        pre_event = adjusted.index < ex_date
        adjusted.loc[pre_event, ["open", "high", "low", "close"]] *= 0.5
        adjusted.loc[pre_event, "volume"] *= 2.0
        return adjusted

    monkeypatch.setattr(audit, "fetch_daily_bars", fake_bars)
    monkeypatch.setattr(
        audit,
        "validate_adjustment_effect",
        lambda raw, adjusted, event_type, ex_date: {
            "event_type": event_type,
            "ex_date": ex_date.isoformat(),
            "price_adjustment_observed": True,
        },
    )

    results = audit.run_audit("key", "secret", "2016-01-01", "2025-12-31")
    split_result = next(result for result in results if result["event_type"] == "split")

    assert split_result["symbol"] == "AAA"
    assert split_result["skipped_uncovered_candidates"] == [
        {
            "symbol": "CLGCF",
            "ex_date": "2025-01-10",
            "reason": "no IEX raw bars for CLGCF",
        }
    ]


def test_run_audit_is_inconclusive_when_no_candidate_has_raw_bars(monkeypatch):
    events = [
        {"_audit_type": "split", "symbol": "CLGCF", "ex_date": "2025-01-10"},
        {"_audit_type": "dividend", "symbol": "BBB", "ex_date": "2024-03-10"},
        {"_audit_type": "spin_off", "source_symbol": "CCC", "ex_date": "2024-04-10"},
    ]
    monkeypatch.setattr(audit, "fetch_corporate_actions", lambda *args: events)
    monkeypatch.setattr(audit, "StockHistoricalDataClient", lambda **kwargs: object())

    def no_bars(_client, symbol, ex_date, adjustment):
        raise audit.NoBarsAvailable(f"no bars for {symbol}")

    monkeypatch.setattr(audit, "fetch_daily_bars", no_bars)

    with pytest.raises(RuntimeError, match="INCONCLUSIVE: no split candidate has auditable raw ex-date coverage"):
        audit.run_audit("key", "secret", "2016-01-01", "2025-12-31")



def test_run_audit_skips_candidate_without_ex_date_bar_and_uses_next(monkeypatch):
    events = [
        {"_audit_type": "dividend", "symbol": "AAA", "ex_date": "2025-01-03", "id": "no-session"},
        {"_audit_type": "dividend", "symbol": "BBB", "ex_date": "2025-01-03", "id": "covered"},
        {"_audit_type": "split", "symbol": "DDD", "ex_date": "2025-01-03", "id": "split"},
        {"_audit_type": "spin_off", "source_symbol": "CCC", "ex_date": "2025-01-03", "id": "spin"},
    ]
    monkeypatch.setattr(audit, "fetch_corporate_actions", lambda *args: events)
    monkeypatch.setattr(audit, "StockHistoricalDataClient", lambda **kwargs: object())

    def fake_bars(_client, symbol, ex_date, adjustment):
        raw = _bars()
        if symbol == "AAA":
            return raw.drop(index=ex_date)
        if adjustment == "raw":
            return raw
        adjusted = raw.copy()
        pre_event = adjusted.index < ex_date
        adjusted.loc[pre_event, ["open", "high", "low", "close"]] *= 0.99
        if symbol == "DDD":
            adjusted.loc[pre_event, "volume"] *= 2.0
        return adjusted

    monkeypatch.setattr(audit, "fetch_daily_bars", fake_bars)
    results = audit.run_audit("key", "secret", "2016-01-01", "2025-12-31")

    dividend = next(result for result in results if result["event_type"] == "dividend")
    assert dividend["symbol"] == "BBB"
    assert dividend["skipped_uncovered_candidates"] == [
        {
            "symbol": "AAA",
            "ex_date": "2025-01-03",
            "reason": (
                "No raw daily bar on ex-date 2025-01-03; "
                "event boundary cannot be verified on this feed"
            ),
        }
    ]


def test_run_audit_is_inconclusive_when_all_candidates_lack_ex_date_bar(monkeypatch):
    events = [
        {"_audit_type": "split", "symbol": "AAA", "ex_date": "2025-01-10"},
        {"_audit_type": "dividend", "symbol": "BBB", "ex_date": "2024-03-10"},
        {"_audit_type": "spin_off", "source_symbol": "CCC", "ex_date": "2024-04-10"},
    ]
    monkeypatch.setattr(audit, "fetch_corporate_actions", lambda *args: events)
    monkeypatch.setattr(audit, "StockHistoricalDataClient", lambda **kwargs: object())

    def bars_without_event_session(_client, _symbol, ex_date, _adjustment):
        return _bars().drop(index=ex_date if ex_date in _bars().index else date(2025, 1, 3))

    monkeypatch.setattr(audit, "fetch_daily_bars", bars_without_event_session)

    with pytest.raises(RuntimeError, match="INCONCLUSIVE: no split candidate has auditable raw ex-date coverage"):
        audit.run_audit("key", "secret", "2016-01-01", "2025-12-31")



def test_rate_limit_retry_honors_retry_after_and_recovers(monkeypatch):
    delays = []
    monkeypatch.setattr(audit.time_module, "sleep", delays.append)
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError(
                "https://data.alpaca.markets", 429, "Too Many Requests",
                {"Retry-After": "0"}, BytesIO(b"rate limited"),
            )
        return {"ok": True}

    assert audit._call_with_rate_limit_retry(operation, "test request") == {"ok": True}
    assert attempts == 2
    assert delays == [0.0]


def test_rate_limit_retry_fails_closed_after_max_attempts(monkeypatch):
    delays = []
    monkeypatch.setattr(audit.time_module, "sleep", delays.append)
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        raise HTTPError(
            "https://data.alpaca.markets", 429, "Too Many Requests",
            {}, BytesIO(b"rate limited"),
        )

    with pytest.raises(RuntimeError, match="rate limit persisted"):
        audit._call_with_rate_limit_retry(operation, "test request")

    assert attempts == audit.RATE_LIMIT_MAX_ATTEMPTS
    assert len(delays) == audit.RATE_LIMIT_MAX_ATTEMPTS - 1
