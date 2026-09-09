"""Verbrauchs- und Erzeugungsprognose für den Folgetag."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence
from zoneinfo import ZoneInfo

from .config import Settings
from .hass import EntityState
from .pvforecast import slot_profile
from .timeutil import SLOT_HOURS, in_window, to_local

# Normiertes Standard-Lastprofil eines Haushalts (Anteil je Stunde, Summe 1).
# Dient nur, solange keine ausreichende Historie vorliegt.
DEFAULT_HOUSEHOLD_SHAPE: tuple[float, ...] = (
    0.02136, 0.01831, 0.01729, 0.01628, 0.01729, 0.02238, 0.03459, 0.04578,
    0.04883, 0.04680, 0.04476, 0.04578, 0.04985, 0.04578, 0.04171, 0.04171,
    0.04680, 0.05900, 0.07121, 0.07325, 0.06511, 0.05392, 0.04171, 0.03050,
)

# Tagesform einer Wärmepumpe (Anteil je Stunde, Summe 1).
DEFAULT_HEATPUMP_SHAPE: tuple[float, ...] = (
    0.040, 0.038, 0.038, 0.038, 0.040, 0.048, 0.058, 0.060,
    0.052, 0.044, 0.038, 0.034, 0.032, 0.030, 0.030, 0.032,
    0.038, 0.046, 0.052, 0.052, 0.050, 0.046, 0.042, 0.042,
)


@dataclass
class HistoryBundle:
    """Aus Home Assistant gelesene Rohdaten, entkoppelt von der Rechnung."""

    household_hourly: list[tuple[datetime, float]] = field(default_factory=list)
    wallbox_hourly: list[tuple[datetime, float]] = field(default_factory=list)
    heatpump_hourly: list[tuple[datetime, float]] = field(default_factory=list)
    pv_forecast_state: EntityState | None = None
    battery_soc_pct: float | None = None
    basis: str = "netzbezug"
    notes: list[str] = field(default_factory=list)


@dataclass
class LoadProfile:
    weekday_hourly: list[float]
    weekend_hourly: list[float]
    weekday_days: int
    weekend_days: int
    source: str

    def hourly_for(self, day: date) -> list[float]:
        return self.weekend_hourly if day.weekday() >= 5 else self.weekday_hourly

    def days_for(self, day: date) -> int:
        return self.weekend_days if day.weekday() >= 5 else self.weekday_days


@dataclass
class DayForecast:
    day: date
    slots_utc: list[datetime]
    duration_h: float
    household_kwh: list[float]
    ev_kwh: list[float]
    heatpump_kwh: list[float]
    pv_kwh: list[float]
    load_source: str
    pv_source: str
    notes: list[str] = field(default_factory=list)

    @property
    def load_kwh(self) -> list[float]:
        return [h + e + w for h, e, w in zip(self.household_kwh, self.ev_kwh, self.heatpump_kwh)]

    @property
    def total_load_kwh(self) -> float:
        return sum(self.load_kwh)

    @property
    def total_pv_kwh(self) -> float:
        return sum(self.pv_kwh)


def _complete_days(rows: Sequence[tuple[datetime, float]], tz: ZoneInfo) -> dict[date, list[float]]:
    """Stundenwerte nach lokalem Kalendertag gruppieren; nur vollständige Tage."""
    per_day: dict[date, list[float | None]] = {}
    for moment, value in rows:
        local = to_local(moment, tz)
        bucket = per_day.setdefault(local.date(), [None] * 24)
        hour = local.hour
        bucket[hour] = (bucket[hour] or 0.0) + value
    complete: dict[date, list[float]] = {}
    for day, hours in per_day.items():
        missing = sum(1 for value in hours if value is None)
        if missing > 2:  # Zeitumstellung und einzelne Lücken toleriert
            continue
        complete[day] = [float(value or 0.0) for value in hours]
    return complete


def build_load_profile(
    rows: Sequence[tuple[datetime, float]],
    tz: ZoneInfo,
    min_days: int,
) -> LoadProfile:
    """Mittleres Stundenprofil je Werktag und Wochenende aus der Historie."""
    per_day = _complete_days(rows, tz)
    weekday_days = [hours for day, hours in per_day.items() if day.weekday() < 5]
    weekend_days = [hours for day, hours in per_day.items() if day.weekday() >= 5]

    def average(days: list[list[float]]) -> list[float] | None:
        if not days:
            return None
        return [sum(day[hour] for day in days) / len(days) for hour in range(24)]

    weekday = average(weekday_days)
    weekend = average(weekend_days)
    if weekday is None and weekend is None:
        shape = list(DEFAULT_HOUSEHOLD_SHAPE)
        return LoadProfile(shape, shape, 0, 0, "Standardprofil (keine Historie)")
    if weekday is None:
        weekday = list(weekend or [])
    if weekend is None:
        weekend = list(weekday)

    total_days = len(weekday_days) + len(weekend_days)
    source = (
        f"Historie ({total_days} Tage)"
        if total_days >= min_days
        else f"Historie ({total_days} Tage, unter Mindestumfang)"
    )
    return LoadProfile(weekday, weekend, len(weekday_days), len(weekend_days), source)


def reference_daily_kwh(day: date, monthly_reference: dict[str, float]) -> float | None:
    """Tagesmittel aus dem Referenz-Netzbezug des Monats."""
    value = monthly_reference.get(str(day.month))
    if value is None:
        return None
    days_in_month = calendar.monthrange(day.year, day.month)[1]
    return float(value) / days_in_month


def blend_daily_total(
    day: date,
    profile: LoadProfile,
    monthly_reference: dict[str, float],
    reference_weight: float,
    min_days: int,
) -> tuple[float, str]:
    """Tagesmenge aus Historie und Monatsreferenz mischen."""
    history_total = sum(profile.hourly_for(day))
    reference = reference_daily_kwh(day, monthly_reference)
    days = profile.days_for(day)

    if days == 0 and reference is not None:
        return reference, "Monatsreferenz (keine Historie)"
    if reference is None:
        return history_total, profile.source
    weight = reference_weight if days >= min_days else 1.0 - (days / max(1, min_days)) * (1 - reference_weight)
    weight = max(0.0, min(1.0, weight))
    blended = (1.0 - weight) * history_total + weight * reference
    return blended, f"{profile.source}, Monatsreferenz gewichtet {weight:.0%}"


def _hourly_to_slots(hourly: Sequence[float], slots_utc: Sequence[datetime], tz: ZoneInfo,
                     duration_h: float) -> list[float]:
    """Stundenwerte gleichmäßig auf die Slots der jeweiligen Stunde verteilen."""
    counts: dict[int, int] = {}
    for slot in slots_utc:
        hour = to_local(slot, tz).hour
        counts[hour] = counts.get(hour, 0) + 1
    return [
        hourly[to_local(slot, tz).hour] / max(1, counts[to_local(slot, tz).hour])
        for slot in slots_utc
    ]


def ev_slot_profile(
    slots_utc: Sequence[datetime],
    tz: ZoneInfo,
    duration_h: float,
    kwh: float,
    power_kw: float,
    start_hour: int,
    end_hour: int,
) -> list[float]:
    """Auto-Ladung im Nachtfenster, begrenzt durch die Wallbox-Leistung."""
    profile = [0.0] * len(slots_utc)
    if kwh <= 0 or power_kw <= 0:
        return profile
    per_slot_max = power_kw * duration_h
    window = [
        index
        for index, slot in enumerate(slots_utc)
        if in_window(to_local(slot, tz).hour + to_local(slot, tz).minute / 60.0, start_hour, end_hour)
    ]
    if not window:
        return profile
    # Im Fenster ab dem Beginn laden, chronologisch ab der Fenstergrenze.
    ordered = sorted(window, key=lambda i: _window_position(slots_utc[i], tz, start_hour))
    remaining = kwh
    for index in ordered:
        if remaining <= 0:
            break
        take = min(per_slot_max, remaining)
        profile[index] = take
        remaining -= take
    return profile


def _window_position(slot: datetime, tz: ZoneInfo, start_hour: int) -> float:
    hour = to_local(slot, tz).hour + to_local(slot, tz).minute / 60.0
    return (hour - start_hour) % 24.0


def heatpump_daily_kwh(day: date, annual_kwh: float, monthly_share: Sequence[float]) -> float:
    if annual_kwh <= 0 or len(monthly_share) < 12:
        return 0.0
    share = float(monthly_share[day.month - 1])
    days_in_month = calendar.monthrange(day.year, day.month)[1]
    return annual_kwh * share / days_in_month


def average_daily(rows: Sequence[tuple[datetime, float]], tz: ZoneInfo) -> float:
    """Mittlere Tagesmenge.

    Anders als beim Lastprofil zaehlt hier nur die Summe, deshalb duerfen auch
    Tage mitzaehlen, an denen der Sensor nur wenige Stunden gemeldet hat - eine
    Wallbox liefert naturgemaess nur während der Ladung Werte.
    """
    per_day: dict[date, float] = {}
    for moment, value in rows:
        local_day = to_local(moment, tz).date()
        per_day[local_day] = per_day.get(local_day, 0.0) + value
    if not per_day:
        return 0.0
    return sum(per_day.values()) / len(per_day)


def build_day_forecast(
    day: date,
    slots_utc: Sequence[datetime],
    tz: ZoneInfo,
    settings: Settings,
    bundle: HistoryBundle,
    duration_h: float = SLOT_HOURS,
) -> DayForecast:
    """Prognose je Slot für Haushalt, Auto, Wärmepumpe und PV."""
    notes = list(bundle.notes)

    profile = build_load_profile(bundle.household_hourly, tz, settings.forecast.min_history_days)
    daily_total, total_source = blend_daily_total(
        day,
        profile,
        settings.forecast.monthly_reference_kwh,
        settings.forecast.reference_weight,
        settings.forecast.min_history_days,
    )
    hourly = profile.hourly_for(day)
    shape_sum = sum(hourly)
    if shape_sum <= 0:
        hourly = list(DEFAULT_HOUSEHOLD_SHAPE)
        shape_sum = sum(hourly)
        notes.append("Lastprofil leer, Standardform verwendet")
    scaled = [value / shape_sum * daily_total for value in hourly]

    household = _hourly_to_slots(scaled, slots_utc, tz, duration_h)

    # Sockel: eine harte Grundlast hebt das Profil an, ohne die Tagesmenge zu senken.
    if settings.forecast.baseload_kw > 0:
        floor = settings.forecast.baseload_kw * duration_h
        household = [max(value, floor) for value in household]

    ev_kwh_night = settings.ev.kwh_per_night
    if settings.ev.enabled and ev_kwh_night <= 0:
        ev_kwh_night = average_daily(bundle.wallbox_hourly, tz)
        if ev_kwh_night > 0:
            notes.append(f"Auto-Ladebedarf aus Wallbox-Historie: {ev_kwh_night:.1f} kWh/Nacht")
    ev = (
        ev_slot_profile(
            slots_utc, tz, duration_h, ev_kwh_night, settings.ev.power_kw,
            settings.ev.window_start_hour, settings.ev.window_end_hour,
        )
        if settings.ev.enabled
        else [0.0] * len(slots_utc)
    )

    if settings.heatpump.enabled:
        hp_daily = heatpump_daily_kwh(day, settings.heatpump.annual_kwh, settings.heatpump.monthly_share)
        hp_hourly = [share / sum(DEFAULT_HEATPUMP_SHAPE) * hp_daily for share in DEFAULT_HEATPUMP_SHAPE]
        heatpump = _hourly_to_slots(hp_hourly, slots_utc, tz, duration_h)
    else:
        heatpump = [0.0] * len(slots_utc)

    pv, pv_source, pv_notes = slot_profile(
        bundle.pv_forecast_state,
        slots_utc,
        duration_h,
        tz,
        settings.forecast.pv_latitude,
        settings.forecast.pv_peak_hour,
    )
    notes.extend(pv_notes)

    load_source = f"{total_source}; Basis {bundle.basis}"
    return DayForecast(
        day=day,
        slots_utc=list(slots_utc),
        duration_h=duration_h,
        household_kwh=household,
        ev_kwh=ev,
        heatpump_kwh=heatpump,
        pv_kwh=pv,
        load_source=load_source,
        pv_source=pv_source,
        notes=notes,
    )
