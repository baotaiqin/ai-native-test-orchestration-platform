from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleExpressionError(ValueError):
    pass


def validate_timezone(value: str) -> str:
    normalized = value.strip()
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleExpressionError("无效的 IANA 时区") from exc
    return normalized


def _parse_time(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ScheduleExpressionError("时间必须使用 HH:MM 格式") from exc
    return parsed.replace(second=0, microsecond=0)


def _as_utc_naive(value: datetime) -> datetime:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.replace(tzinfo=None)


def _valid_local_candidate(day: date, local_time: time, zone: ZoneInfo) -> datetime | None:
    candidate = datetime.combine(day, local_time, tzinfo=zone)
    utc_candidate = candidate.astimezone(UTC)
    round_trip = utc_candidate.astimezone(zone)
    if round_trip.date() != day or round_trip.time().replace(tzinfo=None) != local_time:
        return None
    return utc_candidate.replace(tzinfo=None)


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_wildcard: bool
    weekday_wildcard: bool

    def day_matches(self, value: date) -> bool:
        day_matches = value.day in self.days
        cron_weekday = (value.weekday() + 1) % 7
        weekday_matches = cron_weekday in self.weekdays
        if self.day_wildcard and self.weekday_wildcard:
            return True
        if self.day_wildcard:
            return weekday_matches
        if self.weekday_wildcard:
            return day_matches
        return day_matches or weekday_matches


def _parse_field(
    source: str, minimum: int, maximum: int, label: str, *, normalize_weekday: bool = False
) -> tuple[frozenset[int], bool]:
    wildcard = source == "*"
    values: set[int] = set()
    for token in source.split(","):
        if not token:
            raise ScheduleExpressionError(f"Cron {label}字段格式无效")
        base, separator, step_text = token.partition("/")
        if separator:
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ScheduleExpressionError(f"Cron {label}步长无效") from exc
            if step <= 0 or step > maximum - minimum + 1:
                raise ScheduleExpressionError(f"Cron {label}步长超出范围")
        else:
            step = 1
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ScheduleExpressionError(f"Cron {label}范围无效") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise ScheduleExpressionError(f"Cron {label}值无效") from exc
        allowed_maximum = 7 if normalize_weekday else maximum
        if start < minimum or end > allowed_maximum or start > end:
            raise ScheduleExpressionError(f"Cron {label}值超出范围")
        for value in range(start, end + 1, step):
            values.add(0 if normalize_weekday and value == 7 else value)
    return frozenset(values), wildcard


def parse_cron(expression: str) -> CronSpec:
    normalized = " ".join(expression.strip().split())
    fields = normalized.split(" ")
    if len(fields) != 5:
        raise ScheduleExpressionError("Cron 必须是 5 段表达式：分 时 日 月 周")
    minutes, _ = _parse_field(fields[0], 0, 59, "分钟")
    hours, _ = _parse_field(fields[1], 0, 23, "小时")
    days, day_wildcard = _parse_field(fields[2], 1, 31, "日期")
    months, _ = _parse_field(fields[3], 1, 12, "月份")
    weekdays, weekday_wildcard = _parse_field(fields[4], 0, 6, "星期", normalize_weekday=True)
    return CronSpec(minutes, hours, days, months, weekdays, day_wildcard, weekday_wildcard)


def next_occurrence(
    *,
    schedule_type: str,
    timezone: str,
    after: datetime,
    daily_time: str | None = None,
    weekdays: list[int] | None = None,
    cron_expression: str | None = None,
) -> datetime:
    zone = ZoneInfo(validate_timezone(timezone))
    after_utc = _as_utc_naive(after)
    local_after = after_utc.replace(tzinfo=UTC).astimezone(zone)
    first_day = local_after.date()
    if schedule_type == "DAILY":
        times = [_parse_time(daily_time or "")]
        allowed_weekdays: set[int] | None = None
        cron = None
    elif schedule_type == "WEEKLY":
        times = [_parse_time(daily_time or "")]
        allowed_weekdays = set(weekdays or [])
        if not allowed_weekdays or not allowed_weekdays.issubset(set(range(1, 8))):
            raise ScheduleExpressionError("Weekly 至少选择一个星期，取值为 1 到 7")
        cron = None
    elif schedule_type == "CRON":
        cron = parse_cron(cron_expression or "")
        times = [
            time(hour=hour, minute=minute)
            for hour in sorted(cron.hours)
            for minute in sorted(cron.minutes)
        ]
        allowed_weekdays = None
    else:
        raise ScheduleExpressionError("不支持的调度类型")

    for offset in range(0, 366 * 8):
        day = first_day + timedelta(days=offset)
        if schedule_type == "WEEKLY" and day.isoweekday() not in allowed_weekdays:
            continue
        if cron is not None and (day.month not in cron.months or not cron.day_matches(day)):
            continue
        for local_time in times:
            candidate = _valid_local_candidate(day, local_time, zone)
            if candidate is not None and candidate > after_utc:
                return candidate
    raise ScheduleExpressionError("未来 8 年内没有可触发时间，请检查调度表达式")
