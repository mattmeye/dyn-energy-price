"""PV-Prognose: Tagesform aus der Forecast.Solar-Entität, sonst rechnerisch genähert."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from .hass import EntityState
from .timeutil import UTC, local_hour_fraction, parse_iso

# Attribute, unter denen HA-Integrationen eine zeitaufgeloeste Prognose liefern.
_PERIOD_ATTRS = ("watt_hours_period", "wh_period", "wh_hours", "watts", "watts_period")
_DETAIL_ATTRS = ("detailedForecast", "detailedHourly", "forecast", "detailed_forecast")


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_period_mapping(raw: Any) -> list[tuple[datetime, float]]:
    """Attribut der Form {Zeitstempel: Wert} in Stützpunkte umwandeln."""
    if not isinstance(raw, dict):
        return []
    points: list[tuple[datetime, float]] = []
    for key, value in raw.items():
        number = _as_float(value)
        if number is None:
            continue
        try:
            moment = parse_iso(str(key))
        except ValueError:
            continue
        points.append((moment.astimezone(UTC), number))
    points.sort(key=lambda row: row[0])
    return points


def _parse_detail_list(raw: Any) -> list[tuple[datetime, float]]:
    """Solcast-artige Liste [{period_start, pv_estimate}] in Stützpunkte umwandeln."""
    if not isinstance(raw, list):
        return []
    points: list[tuple[datetime, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        stamp = item.get("period_start") or item.get("start") or item.get("datetime")
        value = None
        for key in ("pv_estimate", "pv_estimate50", "value", "wh", "watt_hours", "power_kw"):
            if key in item:
                value = _as_float(item[key])
                break
        if stamp is None or value is None:
            continue
        try:
            moment = parse_iso(str(stamp))
        except ValueError:
            continue
        points.append((moment.astimezone(UTC), value))
    points.sort(key=lambda row: row[0])
    return points


def _points_to_slots(
    points: Sequence[tuple[datetime, float]],
    slots_utc: Sequence[datetime],
    duration_h: float,
    cumulative: bool,
) -> list[float]:
    """Stützpunkte auf das Slotraster legen; Werte sind relative Gewichte."""
    weights = [0.0] * len(slots_utc)
    if not points:
        return weights
    starts = [p[0] for p in points]
    step_minutes = 60
    if len(points) > 1:
        deltas = [int((b - a).total_seconds() // 60) for a, b in zip(starts, starts[1:])]
        positive = [d for d in deltas if d > 0]
        if positive:
            step_minutes = min(positive)

    for index, slot in enumerate(slots_utc):
        # letzter Stützpunkt, dessen Intervall den Slot enthält
        chosen = None
        for point_start, value in points:
            if point_start <= slot < point_start + _minutes(step_minutes):
                chosen = value
                break
        if chosen is None:
            continue
        share = duration_h * 60.0 / step_minutes
        weights[index] = max(0.0, chosen * (share if not cumulative else 1.0))
    return weights


def _minutes(count: int):
    from datetime import timedelta

    return timedelta(minutes=count)


def clear_sky_shape(
    slots_utc: Sequence[datetime],
    tz: ZoneInfo,
    latitude: float,
    peak_hour: float,
) -> list[float]:
    """Glockenfoermige Tagesform zwischen Sonnenauf- und -untergang (Näherung)."""
    if not slots_utc:
        return []
    day_of_year = slots_utc[len(slots_utc) // 2].astimezone(tz).timetuple().tm_yday
    declination = math.radians(23.45) * math.sin(math.radians(360.0 / 365.0 * (284 + day_of_year)))
    lat = math.radians(max(-66.0, min(66.0, latitude)))
    cos_omega = -math.tan(lat) * math.tan(declination)
    cos_omega = max(-1.0, min(1.0, cos_omega))
    half_day_hours = math.degrees(math.acos(cos_omega)) / 15.0
    sunrise = peak_hour - half_day_hours
    sunset = peak_hour + half_day_hours
    if sunset - sunrise <= 0.2:
        return [0.0] * len(slots_utc)

    shape: list[float] = []
    for slot in slots_utc:
        hour = local_hour_fraction(slot, tz)
        if hour <= sunrise or hour >= sunset:
            shape.append(0.0)
            continue
        position = (hour - sunrise) / (sunset - sunrise)
        shape.append(max(0.0, math.sin(math.pi * position)) ** 1.5)
    return shape


def _normalise(weights: Sequence[float], total_kwh: float) -> list[float]:
    total_weight = sum(weights)
    if total_weight <= 0 or total_kwh <= 0:
        return [0.0] * len(weights)
    factor = total_kwh / total_weight
    return [w * factor for w in weights]


def total_from_state(state: EntityState | None) -> float | None:
    """Prognostizierte Tagesmenge in kWh aus dem Zustand der Entität."""
    if state is None:
        return None
    value = state.as_float()
    if value is None:
        return None
    unit = state.unit.lower()
    if unit == "wh":
        return value / 1000.0
    if unit == "mwh":
        return value * 1000.0
    return value


def slot_profile(
    state: EntityState | None,
    slots_utc: Sequence[datetime],
    duration_h: float,
    tz: ZoneInfo,
    latitude: float,
    peak_hour: float,
    fallback_total_kwh: float = 0.0,
) -> tuple[list[float], str, list[str]]:
    """PV-Erzeugung je Slot in kWh.

    Rueckgabe: Profil, Quellenbeschreibung, Hinweise.
    """
    notes: list[str] = []
    total = total_from_state(state)
    if total is None:
        total = fallback_total_kwh
        if fallback_total_kwh > 0:
            notes.append("PV-Prognose-Entität nicht lesbar, Ersatzwert verwendet")
        else:
            notes.append("Keine PV-Prognose verfügbar, PV mit 0 kWh angesetzt")
            return [0.0] * len(slots_utc), "keine", notes

    attributes = dict(state.attributes) if state is not None else {}
    for key in _PERIOD_ATTRS:
        points = _parse_period_mapping(attributes.get(key))
        if points:
            weights = _points_to_slots(points, slots_utc, duration_h, cumulative=False)
            if sum(weights) > 0:
                return _normalise(weights, total), f"Entität-Attribut {key}", notes
    for key in _DETAIL_ATTRS:
        points = _parse_detail_list(attributes.get(key))
        if points:
            weights = _points_to_slots(points, slots_utc, duration_h, cumulative=False)
            if sum(weights) > 0:
                return _normalise(weights, total), f"Entität-Attribut {key}", notes

    notes.append("Tagesmenge aus der Prognose, Tagesform rechnerisch genähert")
    shape = clear_sky_shape(slots_utc, tz, latitude, peak_hour)
    return _normalise(shape, total), "Tagesmenge + berechnete Tagesform", notes
