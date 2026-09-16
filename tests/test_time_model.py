from datetime import date, datetime, time, timezone

import pytest

from quant_data.time_model import (
    UTC,
    CalendarOverride,
    CalendarStatus,
    MarketEvent,
    SessionSegment,
    VenueCalendar,
    events_available_at,
)


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _event(**changes):
    values = {
        "event_time": _utc(2024, 1, 5, 21),
        "available_time": _utc(2024, 1, 5, 21, 5),
        "ingest_time": _utc(2024, 1, 5, 21, 5),
        "market_timezone": "America/New_York",
        "trading_day": date(2024, 1, 5),
        "source": "example-feed",
        "schema_version": "v1",
    }
    values.update(changes)
    return MarketEvent(**values)


def test_us_equity_close_is_utc_and_cannot_be_read_before_publication():
    # 16:00 EST, Friday 2024-01-05 is 21:00 UTC.  The finalized daily value
    # is explicitly not readable until the feed publishes it five minutes later.
    event = _event()
    assert event.market_event_time.isoformat() == "2024-01-05T16:00:00-05:00"
    assert not event.is_available_at(_utc(2024, 1, 5, 21, 4))
    assert events_available_at((event,), _utc(2024, 1, 5, 21, 4)) == ()
    with pytest.raises(PermissionError, match="future information blocked"):
        event.require_available_at(_utc(2024, 1, 5, 21, 4))
    assert events_available_at((event,), _utc(2024, 1, 5, 21, 5)) == (event,)


def test_cn_futures_night_session_attaches_to_next_trading_day():
    calendar = VenueCalendar(
        calendar_id="SHFE-example", timezone="Asia/Shanghai",
        effective_from=date(2024, 1, 1), source="documented-example",
        weekly_sessions=(SessionSegment(6, time(21), time(2, 30), trading_day_offset=1),),
        weekly_schedule_verified=True,
    )
    # Sunday 21:00 CST is 13:00 UTC and belongs to Monday's futures trade day.
    decision = calendar.decide(_utc(2024, 1, 7, 13))
    assert decision.status is CalendarStatus.OPEN
    assert decision.trading_day == date(2024, 1, 8)
    # The segment remains the same overnight session after local midnight.
    assert calendar.decide(_utc(2024, 1, 7, 17)).trading_day == date(2024, 1, 8)


def test_us_dst_uses_iana_timezone_not_a_fixed_utc_offset():
    calendar = VenueCalendar(
        calendar_id="XNYS-example", timezone="America/New_York",
        effective_from=date(2024, 1, 1), source="documented-example",
        weekly_sessions=tuple(SessionSegment(day, time(9, 30), time(16)) for day in range(5)),
        weekly_schedule_verified=True,
    )
    # Regular-session open changes from 14:30Z before DST to 13:30Z after it.
    assert calendar.decide(_utc(2024, 3, 8, 13, 30)).status is CalendarStatus.CLOSED
    assert calendar.decide(_utc(2024, 3, 8, 14, 30)).status is CalendarStatus.OPEN
    assert calendar.decide(_utc(2024, 3, 11, 12, 30)).status is CalendarStatus.CLOSED
    assert calendar.decide(_utc(2024, 3, 11, 13, 30)).status is CalendarStatus.OPEN
    assert calendar.decide(_utc(2024, 3, 11, 14, 30)).status is CalendarStatus.OPEN


def test_crypto_calendar_is_explicitly_24x7_and_has_utc_trading_day():
    calendar = VenueCalendar(
        calendar_id="BINANCE-example", timezone="UTC", effective_from=date(2024, 1, 1),
        source="venue-24x7-example",
        weekly_sessions=tuple(SessionSegment(day, time.min, time(23, 59, 59, 999999)) for day in range(7)),
        weekly_schedule_verified=True,
    )
    decision = calendar.decide(_utc(2024, 2, 4, 3, 15))
    assert decision.status is CalendarStatus.OPEN
    assert decision.trading_day == date(2024, 2, 4)


def test_unverified_calendar_boundaries_can_be_unknown_or_blocked():
    calendar = VenueCalendar(
        calendar_id="XNYS-bounded", timezone="America/New_York",
        effective_from=date(2024, 1, 1), effective_to=date(2024, 12, 31), source="versioned-snapshot",
        weekly_sessions=(SessionSegment(0, time(9, 30), time(16)),),
        overrides=(CalendarOverride(date(2024, 7, 1), CalendarStatus.BLOCKED, "manual-review", "session boundary unverified"),),
        weekly_schedule_verified=True,
    )
    assert calendar.decide(_utc(2025, 1, 6, 15)).status is CalendarStatus.UNKNOWN
    blocked = calendar.decide(_utc(2024, 7, 1, 14, 30))
    assert blocked.status is CalendarStatus.BLOCKED
    assert "unverified" in blocked.reason

    incomplete = VenueCalendar(
        calendar_id="unverified", timezone="UTC", effective_from=date(2024, 1, 1), source="weekly-hours-only",
        weekly_sessions=(SessionSegment(0, time(9), time(16)),),
    )
    assert incomplete.decide(_utc(2024, 1, 1, 10)).status is CalendarStatus.UNKNOWN


def test_invalid_event_times_and_non_utc_strategy_reads_fail_closed():
    with pytest.raises(ValueError, match="available_time cannot"):
        _event(available_time=_utc(2024, 1, 5, 20, 59))
    with pytest.raises(ValueError, match="must be timezone-aware UTC"):
        _event().is_available_at(datetime(2024, 1, 5, 21, 5))
