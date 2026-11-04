"""Deterministic scheduling rules for the RW-08 OPG execution window."""

from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


_ET = ZoneInfo("America/New_York")


class OpgScheduleState(Enum):
    WAITING_FOR_WINDOW = "waiting_for_window"
    SUBMISSION_WINDOW = "submission_window"
    MISSED_OPEN = "missed_open"


class OpgSessionScheduler:
    """Pure XNYS session/window resolver; never interacts with a broker."""

    def __init__(self):
        self._calendar = xcals.get_calendar("XNYS")

    def _validated_session(self, session_date: date) -> pd.Timestamp:
        session = pd.Timestamp(session_date)
        if not self._calendar.is_session(session):
            raise ValueError(f"{session_date} is not a valid NYSE session")
        return session

    def next_session(self, signal_session: date) -> date:
        """Return the first XNYS session strictly after the signal session."""
        session = self._validated_session(signal_session)
        sessions = self._calendar.sessions_in_range(
            session + pd.Timedelta(days=1),
            session + pd.Timedelta(days=14),
        )
        if sessions.empty:
            raise RuntimeError(
                f"Unable to resolve next NYSE session after {signal_session}"
            )
        return sessions[0].date()

    def state_for(self, signal_session: date, now: datetime) -> OpgScheduleState:
        """Classify the fixed 19:00 ET to 09:25 ET window for this signal."""
        self._validated_session(signal_session)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        local_now = now.astimezone(_ET)
        target_session = self.next_session(signal_session)
        window_open = datetime.combine(signal_session, time(19, 0), tzinfo=_ET)
        cutoff = datetime.combine(target_session, time(9, 25), tzinfo=_ET)

        if local_now < window_open:
            return OpgScheduleState.WAITING_FOR_WINDOW
        if local_now < cutoff:
            return OpgScheduleState.SUBMISSION_WINDOW
        return OpgScheduleState.MISSED_OPEN
