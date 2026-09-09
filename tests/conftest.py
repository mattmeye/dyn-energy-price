"""Gemeinsame Testbausteine."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dyn_price"))

from dynprice.config import Settings  # noqa: E402
from dynprice.forecast import HistoryBundle, build_day_forecast  # noqa: E402
from dynprice.hass import EntityState  # noqa: E402
from dynprice.timeutil import slot_starts_utc, to_local, tzinfo  # noqa: E402

TZ = tzinfo("Europe/Berlin")
DAY = date(2026, 11, 20)


@pytest.fixture
def tz():
    return TZ


@pytest.fixture
def slots():
    return slot_starts_utc(DAY, TZ)


@pytest.fixture
def settings() -> Settings:
    config = Settings()
    config.ev.kwh_per_night = 20.0
    return config


def flat_history(days: int = 26, base_kwh: float = 0.25, evening_kwh: float = 0.7):
    """Stuendliche Verbrauchshistorie mit Abendspitze."""
    start = datetime(2026, 10, 25, tzinfo=timezone.utc)
    return [
        (start + timedelta(days=day, hours=hour), base_kwh + (evening_kwh if 17 <= hour < 21 else 0.0))
        for day in range(days)
        for hour in range(24)
    ]


def pv_state(kwh: float) -> EntityState:
    return EntityState("sensor.pv_morgen", str(kwh), {"unit_of_measurement": "kWh"})


@pytest.fixture
def forecast(settings, slots):
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(3.0))
    return build_day_forecast(DAY, slots, TZ, settings, bundle)


def night_cheap_prices(slots_utc, tz=TZ, night=20.0, evening=180.0, other=90.0):
    """Preisverlauf mit billiger Nacht (1-5 Uhr) und teurem Abend (17-21 Uhr)."""
    values = []
    for slot in slots_utc:
        hour = to_local(slot, tz).hour
        if 1 <= hour < 5:
            values.append(night)
        elif 17 <= hour < 21:
            values.append(evening)
        else:
            values.append(other)
    return values
