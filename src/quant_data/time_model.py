"""Point-in-time event and deliberately conservative market-calendar model.

This module does not claim to be an exchange holiday calendar.  It models the
time facts that an event-driven strategy must carry, plus weekly/session rules
whose provenance is known to the caller.  A date or boundary without a
verified rule is represented as ``UNKNOWN``/``BLOCKED`` rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Iterable
from zoneinfo import ZoneInfo


UTC = timezone.utc


class CalendarStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MarketEvent:
    """One immutable market observation with its point-in-time availability.

    All timestamps are timezone-aware UTC.  ``event_time`` describes when the
    observed market fact happened; ``available_time`` describes the earliest
    instant a strategy is allowed to consume this particular revision.  It is
    therefore not valid to substitute a later corrected value into an earlier
    backtest instant.
    """

    event_time: datetime
    available_time: datetime
    ingest_time: datetime
    market_timezone: str
    trading_day: date
    source: str
    schema_version: str
    revision: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        for name in ("event_time", "available_time", "ingest_time"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware UTC")
            if value.utcoffset() != timedelta(0):
                raise ValueError(f"{name} must use UTC offset +00:00")
        try:
            ZoneInfo(self.market_timezone)
        except Exception as exc:  # ZoneInfoNotFoundError is platform-dependent.
            raise ValueError(f"unknown market timezone: {self.market_timezone}") from exc
        if self.available_time < self.event_time:
            raise ValueError("available_time cannot be before event_time")
        if self.ingest_time < self.available_time:
            raise ValueError("ingest_time cannot be before available_time")
        if not self.source or not self.schema_version:
            raise ValueError("source and schema_version are required")

    def is_available_at(self, strategy_time: datetime) -> bool:
        """Whether a strategy may read this event at a UTC instant."""
        _require_utc(strategy_time, "strategy_time")
        return strategy_time >= self.available_time

    def require_available_at(self, strategy_time: datetime) -> None:
        """Fail closed when a caller attempts a future-information read."""
        if not self.is_available_at(strategy_time):
            raise PermissionError(
                "future information blocked: strategy_time precedes event available_time"
            )

    @property
    def market_event_time(self) -> datetime:
        """``event_time`` rendered in the venue's local timezone."""
        return self.event_time.astimezone(ZoneInfo(self.market_timezone))


@dataclass(frozen=True)
class SessionSegment:
    """A weekly local-time session segment, inclusive at open/exclusive at close.

    ``trading_day_offset`` attaches an overnight segment to the following
    trading date.  For example, a Sunday 21:00 Shanghai night session has
    offset ``1`` and belongs to Monday's futures trading day.
    """

    weekday: int  # Python weekday: Monday=0 ... Sunday=6.
    opens_at: time
    closes_at: time
    trading_day_offset: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be between 0 (Monday) and 6 (Sunday)")
        if self.opens_at == self.closes_at:
            raise ValueError("a session segment cannot have equal open and close")


@dataclass(frozen=True)
class CalendarOverride:
    """A dated, source-versioned exception to weekly session rules."""

    local_date: date
    status: CalendarStatus
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("calendar override source is required")


@dataclass(frozen=True)
class CalendarDecision:
    status: CalendarStatus
    trading_day: date | None
    reason: str

    @property
    def tradable(self) -> bool:
        return self.status is CalendarStatus.OPEN


@dataclass(frozen=True)
class VenueCalendar:
    """A bounded calendar rule set; it intentionally never invents holidays."""

    calendar_id: str
    timezone: str
    effective_from: date
    source: str
    effective_to: date | None = None
    weekly_sessions: tuple[SessionSegment, ...] = field(default_factory=tuple)
    overrides: tuple[CalendarOverride, ...] = field(default_factory=tuple)
    weekly_schedule_verified: bool = False

    def __post_init__(self) -> None:
        if not self.calendar_id or not self.source:
            raise ValueError("calendar_id and source are required")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be before effective_from")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError(f"unknown calendar timezone: {self.timezone}") from exc
        dated = [item.local_date for item in self.overrides]
        if len(dated) != len(set(dated)):
            raise ValueError("at most one calendar override is allowed per local date")

    def decide(self, instant: datetime) -> CalendarDecision:
        """Return open/closed/unknown without inferring unrecorded exceptions."""
        _require_utc(instant, "instant")
        local = instant.astimezone(ZoneInfo(self.timezone))
        if local.date() < self.effective_from or (
            self.effective_to is not None and local.date() > self.effective_to
        ):
            return CalendarDecision(CalendarStatus.UNKNOWN, None, "outside verified calendar coverage")
        override = next((item for item in self.overrides if item.local_date == local.date()), None)
        if override is not None and override.status is not CalendarStatus.OPEN:
            return CalendarDecision(override.status, None, f"dated override: {override.note or override.source}")
        match = next((item for item in self.weekly_sessions if _matching_segment(item, local) is not None), None)
        if not self.weekly_schedule_verified:
            return CalendarDecision(
                CalendarStatus.UNKNOWN,
                None,
                "weekly schedule is not verified for dated holiday/session use",
            )
        if match is None:
            return CalendarDecision(CalendarStatus.CLOSED, None, "outside configured weekly sessions")
        session_start_date = _matching_segment(match, local)
        assert session_start_date is not None  # narrowed by the generator above
        return CalendarDecision(
            CalendarStatus.OPEN,
            session_start_date + timedelta(days=match.trading_day_offset),
            "configured weekly session",
        )


def events_available_at(events: Iterable[MarketEvent], strategy_time: datetime) -> tuple[MarketEvent, ...]:
    """The only point-in-time read filter: no event leaks before publication."""
    _require_utc(strategy_time, "strategy_time")
    return tuple(event for event in events if event.is_available_at(strategy_time))


def _matching_segment(segment: SessionSegment, local: datetime) -> date | None:
    """Return the local session-start date if ``local`` is inside the segment."""
    current = local.timetz().replace(tzinfo=None)
    if segment.opens_at < segment.closes_at:
        return local.date() if local.weekday() == segment.weekday and segment.opens_at <= current < segment.closes_at else None
    if local.weekday() == segment.weekday and current >= segment.opens_at:
        return local.date()
    next_weekday = (segment.weekday + 1) % 7
    if local.weekday() == next_weekday and current < segment.closes_at:
        return local.date() - timedelta(days=1)
    return None


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
