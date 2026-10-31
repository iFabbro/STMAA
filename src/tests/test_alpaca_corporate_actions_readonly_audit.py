from datetime import date

import pandas as pd
import pytest

from scripts.alpaca_corporate_actions_readonly_audit import (
    select_latest_event_per_type,
    validate_adjustment_effect,
)


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

    result = validate_adjustment_effect(
        raw, adjusted, event_type, date(2025, 1, 3)
    )

    assert result["event_type"] == event_type
    assert result["price_adjustment_observed"] is True
    assert result["volume_adjustment_observed"] is (event_type == "split")
    assert result["post_event_bars_unchanged"] is True


def test_adjustment_audit_rejects_unadjusted_provider_response():
    raw = _bars()

    with pytest.raises(AssertionError, match="did not adjust pre-event prices"):
        validate_adjustment_effect(raw, raw.copy(), "dividend", date(2025, 1, 3))


def test_adjustment_audit_rejects_split_without_volume_adjustment():
    raw = _bars()
    adjusted = raw.copy()
    pre_event = adjusted.index < date(2025, 1, 3)
    adjusted.loc[pre_event, ["open", "high", "low", "close"]] *= 0.5

    with pytest.raises(AssertionError, match="did not adjust pre-event volume"):
        validate_adjustment_effect(raw, adjusted, "split", date(2025, 1, 3))


def test_adjustment_audit_rejects_missing_ex_date():
    raw = _bars().drop(index=date(2025, 1, 3))

    with pytest.raises(AssertionError, match="ex-date"):
        validate_adjustment_effect(
            raw, raw.copy(), "dividend", date(2025, 1, 3)
        )


def test_event_selection_requires_latest_action_for_symbol():
    events = [
        {
            "_audit_type": "split",
            "symbol": "AAA",
            "ex_date": "2024-01-10",
        },
        {
            "_audit_type": "dividend",
            "symbol": "AAA",
            "ex_date": "2024-02-10",
        },
        {
            "_audit_type": "dividend",
            "symbol": "BBB",
            "ex_date": "2024-03-10",
        },
        {
            "_audit_type": "spin_off",
            "source_symbol": "CCC",
            "ex_date": "2024-04-10",
        },
    ]

    selected = select_latest_event_per_type(events)

    assert "split" not in selected
    assert selected["dividend"]["symbol"] == "BBB"
    assert selected["spin_off"]["source_symbol"] == "CCC"
