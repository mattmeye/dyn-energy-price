"""Nachtraeglicher Abgleich der Prognose mit dem tatsächlichen Verlauf."""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .hass import HomeAssistant, counter_to_hourly
from .prices import PriceProvider, PricesUnavailable, PriceSeries
from .store import Store
from .timeutil import SLOT_HOURS, day_bounds_utc, slot_starts_utc

_LOG = logging.getLogger(__name__)


def _hourly_all_in_ct(series: PriceSeries, day: date, tz: ZoneInfo, settings: Settings) -> dict[datetime, float]:
    """Mittlerer All-in-Preis je Stunde aus den viertelstuendlichen Börsenpreisen."""
    buckets: dict[datetime, list[float]] = {}
    for slot in slot_starts_utc(day, tz, minutes=int(SLOT_HOURS * 60)):
        spot = series.at(slot)
        if spot is None:
            continue
        hour_start = slot.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour_start, []).append(settings.tariff.all_in_ct(spot))
    return {hour: sum(values) / len(values) for hour, values in buckets.items() if values}


def _soc_series(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[str, float]]:
    if not entity_id:
        return []
    try:
        rows = hass.history(entity_id, start, end)
    except Exception as err:  # Recorder-Aufbewahrung oder Verbindungsfehler
        _LOG.info("SoC-Verlauf für %s nicht lesbar: %s", entity_id, err)
        return []
    # Auf Viertelstunden ausdünnen, damit die Ablage klein bleibt.
    thinned: list[tuple[str, float]] = []
    last: datetime | None = None
    for moment, value in rows:
        if last is None or moment - last >= timedelta(minutes=15):
            thinned.append((moment.isoformat().replace("+00:00", "Z"), value))
            last = moment
    return thinned


def actual_for_day(
    day: date,
    tz: ZoneInfo,
    settings: Settings,
    hass: HomeAssistant,
    prices: PriceProvider,
    store: Store | None = None,
) -> dict[str, Any]:
    """Ist-Werte eines vergangenen Tages ermitteln und mit der Prognose vergleichen."""
    start_utc, end_utc = day_bounds_utc(day, tz)
    entities = settings.entities

    # Ein Intervall mehr abfragen, damit die letzte Stunde eine Differenz bildet.
    stats = hass.statistics(
        [entities.grid_import, entities.wallbox_energy, entities.pv_production],
        start_utc,
        end_utc + timedelta(hours=1),
    )
    grid_rows = counter_to_hourly(stats.get(entities.grid_import, []))
    grid_rows = [(moment, value) for moment, value in grid_rows if start_utc <= moment < end_utc]
    if not grid_rows:
        return {
            "day": day.isoformat(),
            "status": "keine_daten",
            "message": "Keine Langzeitstatistik für den Netzbezug verfügbar",
        }

    try:
        series = prices.fetch_day(day, tz)
    except PricesUnavailable as err:
        return {"day": day.isoformat(), "status": "keine_preise", "message": str(err)}

    hourly_price = _hourly_all_in_ct(series, day, tz, settings)
    import_kwh = sum(value for _, value in grid_rows)
    cost_smart = 0.0
    missing_price_kwh = 0.0
    hourly: list[dict[str, float]] = []
    for moment, value in grid_rows:
        price = hourly_price.get(moment)
        if price is None:
            missing_price_kwh += value
            continue
        cost_smart += value * price / 100.0
        hourly.append(
            {
                "hour_utc": moment.isoformat().replace("+00:00", "Z"),
                "import_kwh": round(value, 4),
                "all_in_ct": round(price, 3),
            }
        )

    tariff = settings.tariff
    cost_fixed = import_kwh * tariff.fixed_price_ct / 100.0
    days_in_month = calendar.monthrange(day.year, day.month)[1]
    base_delta = (tariff.base_price_eur_month - tariff.fixed_base_price_eur_month) / days_in_month
    saving = cost_fixed - cost_smart - base_delta

    pv_rows = counter_to_hourly(stats.get(entities.pv_production, []))
    ev_rows = counter_to_hourly(stats.get(entities.wallbox_energy, []))

    payload: dict[str, Any] = {
        "day": day.isoformat(),
        "status": "ok",
        "import_kwh": round(import_kwh, 3),
        "pv_kwh": round(sum(v for m, v in pv_rows if start_utc <= m < end_utc), 3),
        "ev_kwh": round(sum(v for m, v in ev_rows if start_utc <= m < end_utc), 3),
        "cost_smart_eur": round(cost_smart, 4),
        "cost_fixed_eur": round(cost_fixed, 4),
        "base_price_delta_eur": round(base_delta, 4),
        "saving_eur": round(saving, 4),
        "avg_price_ct": round(cost_smart * 100.0 / import_kwh, 3) if import_kwh > 1e-9 else 0.0,
        "hourly": hourly,
        "soc": _soc_series(hass, entities.battery_soc, start_utc, end_utc),
    }
    if missing_price_kwh > 1e-6:
        payload["note"] = f"{missing_price_kwh:.2f} kWh ohne Preiszuordnung"

    if store is not None:
        forecast = store.get_forecast(day)
        if forecast is not None:
            predicted_import = float(forecast.payload["energy"]["import_kwh"])
            predicted_saving = float(forecast.payload["savings"]["vs_fixed_eur"])
            payload["forecast"] = {
                "import_kwh": predicted_import,
                "saving_eur": predicted_saving,
                "import_error_kwh": round(import_kwh - predicted_import, 3),
                "saving_error_eur": round(saving - predicted_saving, 4),
                "verdict": forecast.payload["storage"]["verdict"],
            }
    return payload


def backfill(
    tz: ZoneInfo,
    settings: Settings,
    hass: HomeAssistant,
    prices: PriceProvider,
    store: Store,
    today: date,
    limit: int = 14,
) -> list[str]:
    """Fehlende Ist-Werte vergangener Tage nachtragen."""
    done: list[str] = []
    for day in store.days_without_actual(before=today, limit=limit):
        payload = actual_for_day(day, tz, settings, hass, prices, store)
        if payload.get("status") != "ok":
            _LOG.info("Ist-Abgleich %s übersprungen: %s", day, payload.get("message"))
            continue
        store.save_actual(day, payload)
        done.append(day.isoformat())
    return done
