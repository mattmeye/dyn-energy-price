"""Ablage der Prognosen und Ist-Werte."""

from __future__ import annotations

from datetime import timedelta

from conftest import DAY, TZ, flat_history, night_cheap_prices, pv_state

from nsforecast.evaluate import evaluate_day
from nsforecast.forecast import HistoryBundle, build_day_forecast
from nsforecast.store import Store
from nsforecast.timeutil import slot_starts_utc


def make_evaluation(settings, day=DAY):
    slots = slot_starts_utc(day, TZ)
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(3.0))
    forecast = build_day_forecast(day, slots, TZ, settings, bundle)
    return evaluate_day(forecast, night_cheap_prices(slots), settings, TZ,
                        settings.scenario_battery(), None, start_energy_kwh=0.0)


def test_prognose_wird_gespeichert_und_gelesen(tmp_path, settings):
    store = Store(tmp_path / "db.sqlite")
    evaluation = make_evaluation(settings)
    store.save_forecast(evaluation)
    row = store.get_forecast(DAY)
    assert row is not None
    assert row.day == DAY
    assert row.payload["storage"]["verdict"] == evaluation.storage.verdict
    assert round(row.savings["vs_fixed_eur"], 4) == round(evaluation.saving_vs_fixed_eur, 4)


def test_erneutes_speichern_ersetzt_den_eintrag(tmp_path, settings):
    store = Store(tmp_path / "db.sqlite")
    store.save_forecast(make_evaluation(settings))
    settings.tariff.fixed_price_ct = 40.0
    store.save_forecast(make_evaluation(settings))
    assert len(store.list_forecasts()) == 1
    assert store.get_forecast(DAY).payload["costs"]["fixed_eur"] > 0


def test_szenarien_liegen_nebeneinander(tmp_path, settings):
    store = Store(tmp_path / "db.sqlite")
    store.save_forecast(make_evaluation(settings))
    settings.scenarios.battery_60kwh = True
    gross = make_evaluation(settings)
    gross.scenario = "battery_60kwh"
    store.save_forecast(gross)
    assert store.get_forecast(DAY, "standard") is not None
    assert store.get_forecast(DAY, "battery_60kwh") is not None


def test_zeitraum_filtert(tmp_path, settings):
    store = Store(tmp_path / "db.sqlite")
    for offset in range(5):
        store.save_forecast(make_evaluation(settings, DAY + timedelta(days=offset)))
    assert len(store.list_forecasts()) == 5
    assert len(store.list_forecasts(DAY + timedelta(days=1), DAY + timedelta(days=3))) == 3


def test_offene_ist_werte_werden_gefunden(tmp_path, settings):
    store = Store(tmp_path / "db.sqlite")
    for offset in range(3):
        store.save_forecast(make_evaluation(settings, DAY + timedelta(days=offset)))
    store.save_actual(DAY, {"status": "ok", "import_kwh": 20.0, "saving_eur": 1.0})
    offen = store.days_without_actual(before=DAY + timedelta(days=5))
    assert DAY not in offen
    assert DAY + timedelta(days=1) in offen


def test_laufprotokoll(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    run_id = store.start_run(DAY)
    store.finish_run(run_id, "ok", "fertig")
    runs = store.recent_runs()
    assert runs[0]["status"] == "ok"
    assert runs[0]["message"] == "fertig"
    assert runs[0]["day"] == DAY.isoformat()
