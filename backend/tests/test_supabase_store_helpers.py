from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from voice_core.adapters.supabase.store import _to_timestamptz


def test_date_only_becomes_utc_midnight() -> None:
    assert _to_timestamptz("2026-09-01") == datetime(2026, 9, 1, tzinfo=UTC)


def test_offset_datetime_is_preserved() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    assert _to_timestamptz("2026-09-01T09:00:00+05:30") == datetime(2026, 9, 1, 9, tzinfo=ist)


def test_none_passes_through_so_sql_defaults_to_now() -> None:
    assert _to_timestamptz(None) is None
