"""UTC time boundaries used by persistence and API serialization."""

from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Return the current UTC instant without tzinfo for MySQL DATETIME columns."""

    return datetime.now(UTC).replace(tzinfo=None)


def to_utc_aware(value: datetime | None) -> datetime | None:
    """Normalize a stored or incoming datetime to an aware UTC datetime."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_now_aware() -> datetime:
    """Return the current UTC instant with an explicit UTC offset."""

    return datetime.now(UTC)


def to_utc_isoformat(value: datetime | None) -> str | None:
    """Serialize a datetime as an explicit UTC ISO-8601 value for API responses."""

    aware = to_utc_aware(value)
    return aware.isoformat() if aware is not None else None
