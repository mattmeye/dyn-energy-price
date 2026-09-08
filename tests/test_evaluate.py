"""Ladefenster, Speicherpruefung und Ersparnisrechnung."""

from __future__ import annotations

from conftest import DAY, TZ, flat_history, night_cheap_prices, pv_state

from nsforecast.evaluate import evaluate_day
from nsforecast.forecast import HistoryBundle, build_day_forecast
from nsforecast.timeutil import to_local


def run(settings, slots, spot, start_energy=None, pv_kwh=3.0):
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(pv_kwh))
    forecast = build_day_forecast(DAY, slots, TZ, settings, bundle)
    return evaluate_day(forecast, spot, settings, TZ, settings.scenario_battery(), None,
                        start_energy_kwh=start_energy)


def test_fenster_liegt_in_den_guenstigen_stunden(settings, slots):
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    start = to_local(result.window_start_utc, TZ)
    ende = to_local(result.window_end_utc, TZ)
    assert 1 <= start.hour < 5
    assert ende.hour <= 5
    assert result.window_avg_price_ct < result.day_avg_price_ct


def test_flache_preise_ergeben_kein_fenster(settings, slots):
    result = run(settings, slots, [95.0] * len(slots), start_energy=0.0)
    assert result.window_start_utc is None
    assert result.storage.verdict == "nicht_noetig"
    assert result.saving_from_shifting_eur == 0.0


def test_verschiebung_senkt_die_kosten(settings, slots):
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    assert result.cost_smart_shifted_eur < result.cost_smart_unshifted_eur
    assert result.saving_vs_fixed_eur > result.saving_vs_fixed_unshifted_eur


def test_ohne_speichergrenzen_ist_nie_schlechter(settings, slots):
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    assert result.cost_smart_ideal_eur <= result.cost_smart_shifted_eur + 1e-9
    assert result.saving_vs_fixed_ideal_eur >= result.saving_vs_fixed_eur - 1e-9


def test_kleiner_speicher_begrenzt_die_verschiebung(settings, slots):
    spot = night_cheap_prices(slots)
    gross = run(settings, slots, spot, start_energy=0.0)
    settings.battery.nominal_kwh = 5.0
    klein = run(settings, slots, spot, start_energy=0.0)
    assert klein.storage.shifted_kwh < gross.storage.shifted_kwh
    assert klein.storage.verdict in {"teilweise", "nicht_verschiebbar"}
    assert "kapazitaet" in klein.storage.binding


def test_schwache_ladeleistung_wird_als_grund_genannt(settings, slots):
    settings.battery.charge_kw = 1.0
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    assert "ladeleistung" in result.storage.binding
    assert any("Ladeleistung" in reason for reason in result.storage.reasons)


def test_wallbox_ueber_entladeleistung_bleibt_am_netz(settings, slots):
    settings.ev.power_kw = 11.0
    settings.battery.discharge_kw = 4.0
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    assert result.storage.discharge_blocked_kwh > 0
    assert "entladeleistung" in result.storage.binding


def test_voller_speicher_laesst_keine_kapazitaet_frei(settings, slots):
    result = run(settings, slots, night_cheap_prices(slots), start_energy=27.0)
    assert result.storage.capacity_available_kwh < 5.0


def test_szenario_60_kwh_verschiebt_mehr(settings, slots):
    spot = night_cheap_prices(slots)
    settings.ev.kwh_per_night = 30.0
    standard = run(settings, slots, spot, start_energy=0.0)
    settings.scenarios.battery_60kwh = True
    gross = run(settings, slots, spot, start_energy=0.0)
    assert gross.storage.shifted_kwh > standard.storage.shifted_kwh
    assert gross.battery_usable_kwh > standard.battery_usable_kwh


def test_warnung_wenn_smart_teurer_waere(settings, slots):
    settings.tariff.fixed_price_ct = 20.0   # Fixtarif unter dem All-in-Preis
    result = run(settings, slots, [95.0] * len(slots), start_energy=0.0)
    assert result.saving_vs_fixed_eur < 0
    assert any("teurer" in warnung for warnung in result.warnings)


def test_grundpreis_geht_anteilig_in_die_ersparnis_ein(settings, slots):
    spot = night_cheap_prices(slots)
    mit = run(settings, slots, spot, start_energy=0.0)
    settings.tariff.base_price_eur_month = 0.0
    ohne = run(settings, slots, spot, start_energy=0.0)
    assert ohne.saving_vs_fixed_eur > mit.saving_vs_fixed_eur
    assert round(ohne.saving_vs_fixed_eur - mit.saving_vs_fixed_eur, 4) == round(15.74 / 30, 4)


def test_ladung_und_verdraengung_bleiben_energetisch_plausibel(settings, slots):
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    geladen = sum(result.charge_kwh)
    verdraengt = sum(result.displaced_kwh)
    assert verdraengt <= geladen + 1e-9              # Verluste gehen nie zugunsten des Speichers
    assert round(verdraengt, 3) == round(result.storage.shifted_kwh, 3)


def test_verdraengung_liegt_hinter_dem_ladefenster(settings, slots):
    result = run(settings, slots, night_cheap_prices(slots), start_energy=0.0)
    letzte_ladung = max(i for i, value in enumerate(result.charge_kwh) if value > 0)
    erste_verdraengung = min(i for i, value in enumerate(result.displaced_kwh) if value > 0)
    assert erste_verdraengung > letzte_ladung


def test_ergebnis_ist_json_faehig(settings, slots):
    import json

    payload = run(settings, slots, night_cheap_prices(slots), start_energy=0.0).as_dict()
    text = json.dumps(payload)
    assert json.loads(text)["day"] == DAY.isoformat()
    assert len(payload["series"]["all_in_ct"]) == len(slots)
