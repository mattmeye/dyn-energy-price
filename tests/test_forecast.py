"""Lastprofil, Auto-Ladung, Waermepumpe und PV-Tagesform."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from conftest import DAY, TZ, flat_history, pv_state

from dynprice.config import Settings
from dynprice.forecast import (
    HistoryBundle,
    build_day_forecast,
    build_load_profile,
    blend_daily_total,
    ev_slot_profile,
    heatpump_daily_kwh,
    reference_daily_kwh,
)
from dynprice.pvforecast import clear_sky_shape, slot_profile
from dynprice.hass import EntityState
from dynprice.timeutil import slot_starts_utc, to_local


def test_profil_trennt_werktag_und_wochenende():
    rows = []
    start = datetime(2026, 10, 26, tzinfo=TZ)  # Montag, lokale Mitternacht
    for day in range(28):
        current = start + timedelta(days=day)
        weekend = current.weekday() >= 5
        for hour in range(24):
            rows.append((current + timedelta(hours=hour), 0.5 if weekend else 0.2))
    profile = build_load_profile(rows, TZ, min_days=7)
    assert profile.weekday_days == 20
    assert profile.weekend_days == 8
    assert round(sum(profile.hourly_for(date(2026, 11, 21))), 2) == 12.0  # Samstag
    assert round(sum(profile.hourly_for(date(2026, 11, 20))), 2) == 4.8   # Freitag


def test_ohne_historie_greift_das_standardprofil():
    profile = build_load_profile([], TZ, min_days=7)
    assert profile.weekday_days == 0
    assert round(sum(profile.weekday_hourly), 3) == 1.0


def test_monatsreferenz_wird_gewichtet_eingemischt():
    profile = build_load_profile(flat_history(), TZ, min_days=7)
    reference = {"11": 300.0}   # 10 kWh je Tag
    total, source = blend_daily_total(DAY, profile, reference, reference_weight=0.5, min_days=7)
    history = sum(profile.hourly_for(DAY))
    assert round(total, 4) == round(0.5 * history + 0.5 * 10.0, 4)
    assert "Monatsreferenz" in source


def test_ohne_historie_zaehlt_nur_die_referenz():
    profile = build_load_profile([], TZ, min_days=7)
    total, source = blend_daily_total(DAY, profile, {"11": 300.0}, 0.5, 7)
    assert total == 10.0
    assert "keine Historie" in source


def test_referenzwert_je_tag():
    assert round(reference_daily_kwh(date(2026, 9, 15), {"9": 188.0}), 3) == round(188.0 / 30, 3)
    assert reference_daily_kwh(date(2026, 9, 15), {}) is None


def test_auto_laedt_im_fenster_und_haelt_die_leistung_ein():
    slots = slot_starts_utc(DAY, TZ)
    profile = ev_slot_profile(slots, TZ, 0.25, kwh=20.0, power_kw=11.0,
                              start_hour=22, end_hour=6)
    assert round(sum(profile), 6) == 20.0
    assert max(profile) <= 11.0 * 0.25 + 1e-9
    aktive = [to_local(slots[i], TZ).hour for i, value in enumerate(profile) if value > 0]
    assert all(hour >= 22 or hour < 6 for hour in aktive)
    assert min(aktive) >= 22  # beginnt am Fensteranfang


def test_auto_ohne_bedarf_bleibt_leer():
    slots = slot_starts_utc(DAY, TZ)
    assert sum(ev_slot_profile(slots, TZ, 0.25, 0.0, 11.0, 22, 6)) == 0.0


def test_waermepumpe_verteilt_die_jahresmenge_nach_monaten():
    settings = Settings()
    winter = heatpump_daily_kwh(date(2026, 1, 15), 3000.0, settings.heatpump.monthly_share)
    sommer = heatpump_daily_kwh(date(2026, 7, 15), 3000.0, settings.heatpump.monthly_share)
    assert winter > sommer * 5
    assert heatpump_daily_kwh(date(2026, 1, 15), 0.0, settings.heatpump.monthly_share) == 0.0


def test_pv_tagesform_ist_im_winter_kuerzer_als_im_sommer():
    sommer = clear_sky_shape(slot_starts_utc(date(2026, 6, 21), TZ), TZ, 51.0, 13.0)
    winter = clear_sky_shape(slot_starts_utc(date(2026, 12, 21), TZ), TZ, 51.0, 13.0)
    assert sum(1 for v in sommer if v > 0) > sum(1 for v in winter if v > 0)


def test_pv_attribut_bestimmt_die_form():
    slots = slot_starts_utc(DAY, TZ)
    hours = {f"2026-11-20T{hour:02d}:00:00+01:00": (0 if hour < 9 or hour > 15 else 1000)
             for hour in range(24)}
    state = EntityState("sensor.pv", "6.0", {"unit_of_measurement": "kWh", "watt_hours_period": hours})
    profile, source, _ = slot_profile(state, slots, 0.25, TZ, 51.0, 13.0)
    assert round(sum(profile), 3) == 6.0
    assert "watt_hours_period" in source
    assert profile[0] == 0.0


def test_pv_ohne_entitaet_ist_null():
    slots = slot_starts_utc(DAY, TZ)
    profile, source, notes = slot_profile(None, slots, 0.25, TZ, 51.0, 13.0)
    assert sum(profile) == 0.0
    assert source == "keine"
    assert notes


def test_tagesprognose_summiert_die_teilmengen(settings, slots):
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(9.0))
    forecast = build_day_forecast(DAY, slots, TZ, settings, bundle)
    assert round(forecast.total_pv_kwh, 3) == 9.0
    assert round(sum(forecast.ev_kwh), 3) == 20.0
    assert round(forecast.total_load_kwh, 3) == round(
        sum(forecast.household_kwh) + sum(forecast.ev_kwh) + sum(forecast.heatpump_kwh), 3
    )


def test_wallbox_historie_ersetzt_den_fehlenden_ladebedarf(settings, slots):
    settings.ev.kwh_per_night = 0.0
    wallbox = [(datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(days=d, hours=23), 12.0)
               for d in range(10)]
    bundle = HistoryBundle(household_hourly=flat_history(), wallbox_hourly=wallbox,
                           pv_forecast_state=pv_state(3.0))
    forecast = build_day_forecast(DAY, slots, TZ, settings, bundle)
    assert round(sum(forecast.ev_kwh), 3) == 12.0
