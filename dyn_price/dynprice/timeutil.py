"""Zeitraster: Rohdaten in UTC, Tagesgrenzen und Anzeige in Europe/Berlin."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
SLOT_MINUTES = 15
SLOT_HOURS = SLOT_MINUTES / 60.0


def tzinfo(name: str = "Europe/Berlin") -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # unbekannte Zonen fallen auf Berlin zurück
        return ZoneInfo("Europe/Berlin")


def local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Beginn und Ende des lokalen Kalendertages als UTC-Zeitpunkte."""
    start = local_midnight(day, tz).astimezone(UTC)
    end = local_midnight(day + timedelta(days=1), tz).astimezone(UTC)
    return start, end


def slot_starts_utc(day: date, tz: ZoneInfo, minutes: int = SLOT_MINUTES) -> list[datetime]:
    """Alle Slot-Anfänge des lokalen Tages in UTC.

    Über die UTC-Achse gezaehlt, damit Zeitumstellungstage 92 bzw. 100 Slots
    bekommen statt kuenstlich auf 96 gezwungen zu werden.
    """
    start, end = day_bounds_utc(day, tz)
    step = timedelta(minutes=minutes)
    out: list[datetime] = []
    cursor = start
    while cursor < end:
        out.append(cursor)
        cursor += step
    return out


def to_local(moment: datetime, tz: ZoneInfo) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz)


def local_hour_fraction(moment: datetime, tz: ZoneInfo) -> float:
    """Lokale Tageszeit als Dezimalstunde, z. B. 13.25 für 13:15."""
    local = to_local(moment, tz)
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def parse_iso(value: str) -> datetime:
    """Robuster ISO-Parser für die von Home Assistant gelieferten Zeitstempel."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def in_window(hour: float, start_hour: float, end_hour: float) -> bool:
    """Stundenfenster, das über Mitternacht laufen darf (z. B. 22 bis 6)."""
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour
