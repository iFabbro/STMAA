from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.execution.opg_scheduler import OpgScheduleState, OpgSessionScheduler


ET = ZoneInfo("America/New_York")


@pytest.fixture
def scheduler():
    return OpgSessionScheduler()


def test_next_session_skips_thanksgiving_holiday(scheduler):
    assert scheduler.next_session(date(2026, 11, 25)) == date(2026, 11, 27)


def test_next_session_from_friday_is_monday(scheduler):
    assert scheduler.next_session(date(2026, 11, 27)) == date(2026, 11, 30)


def test_signal_date_must_be_a_valid_nyse_session(scheduler):
    with pytest.raises(ValueError, match="NYSE session"):
        scheduler.next_session(date(2026, 11, 26))


def test_waits_until_1900_et_on_previous_session(scheduler):
    state = scheduler.state_for(
        date(2026, 11, 3),
        datetime(2026, 11, 3, 18, 59, tzinfo=ET),
    )
    assert state is OpgScheduleState.WAITING_FOR_WINDOW


def test_submission_window_opens_at_1900_et(scheduler):
    state = scheduler.state_for(
        date(2026, 11, 3),
        datetime(2026, 11, 3, 19, 0, tzinfo=ET),
    )
    assert state is OpgScheduleState.SUBMISSION_WINDOW


def test_submission_window_remains_open_before_0925_et(scheduler):
    state = scheduler.state_for(
        date(2026, 11, 3),
        datetime(2026, 11, 4, 9, 24, 59, tzinfo=ET),
    )
    assert state is OpgScheduleState.SUBMISSION_WINDOW


def test_cutoff_at_0925_et_marks_signal_missed(scheduler):
    state = scheduler.state_for(
        date(2026, 11, 3),
        datetime(2026, 11, 4, 9, 25, tzinfo=ET),
    )
    assert state is OpgScheduleState.MISSED_OPEN


def test_submission_window_spans_weekend_without_rolling_signal(scheduler):
    signal_session = date(2026, 11, 27)
    saturday_state = scheduler.state_for(
        signal_session,
        datetime(2026, 11, 28, 12, 0, tzinfo=ET),
    )
    monday_after_cutoff_state = scheduler.state_for(
        signal_session,
        datetime(2026, 11, 30, 9, 26, tzinfo=ET),
    )

    assert scheduler.next_session(signal_session) == date(2026, 11, 30)
    assert saturday_state is OpgScheduleState.SUBMISSION_WINDOW
    assert monday_after_cutoff_state is OpgScheduleState.MISSED_OPEN
    assert scheduler.next_session(signal_session) == date(2026, 11, 30)


def test_state_for_requires_timezone_aware_now(scheduler):
    with pytest.raises(ValueError, match="timezone-aware"):
        scheduler.state_for(date(2026, 11, 3), datetime(2026, 11, 3, 19, 0))


def test_scheduler_uses_new_york_local_clock_across_dst(scheduler):
    # The local OPG window remains 19:00 ET after the autumn DST transition.
    state = scheduler.state_for(
        date(2026, 11, 2),
        datetime(2026, 11, 2, 19, 0, tzinfo=ET),
    )
    assert state is OpgScheduleState.SUBMISSION_WINDOW
