"""Tarifformel, Speicherkennwerte und Einstellungs-Serialisierung."""

from __future__ import annotations

import json

from nsforecast.config import Battery, Settings, SettingsStore


def test_all_in_price_folgt_dem_tarifblatt():
    tariff = Settings().tariff
    # 80 EUR/MWh netto = 8 ct/kWh netto -> 15,29 + 1,19 + 8 * 1,19
    assert round(tariff.all_in_ct(80.0), 4) == round(15.29 + 1.19 + 8.0 * 1.19, 4)
    assert round(tariff.all_in_ct(0.0), 4) == 16.48


def test_negativer_boersenpreis_senkt_den_arbeitspreis():
    tariff = Settings().tariff
    assert tariff.all_in_ct(-50.0) < tariff.all_in_ct(0.0)


def test_nutzbare_kapazitaet_aus_soc_grenzen():
    battery = Battery(nominal_kwh=30.0, soc_min_pct=10.0, soc_max_pct=100.0)
    assert battery.usable_kwh == 27.0
    assert battery.energy_above_min(10.0) == 0.0
    assert battery.energy_above_min(100.0) == 27.0
    assert round(battery.energy_above_min(55.0), 3) == 13.5


def test_ueberschriebene_kapazitaet_hat_vorrang():
    battery = Battery(nominal_kwh=30.0, usable_kwh_override=22.0)
    assert battery.usable_kwh == 22.0


def test_wirkungsgrad_setzt_sich_aus_drei_stufen_zusammen():
    battery = Battery(battery_dc_efficiency=0.81, charger_efficiency=1.0, inverter_efficiency=1.0)
    assert round(battery.grid_charge_efficiency, 6) == 0.9
    assert round(battery.roundtrip_efficiency, 6) == 0.81


def test_pv_pfad_umgeht_bei_dc_kopplung_das_ladegeraet():
    dc = Battery(pv_coupling="dc")
    ac = Battery(pv_coupling="ac")
    assert dc.pv_charge_efficiency > ac.pv_charge_efficiency
    assert round(ac.pv_charge_efficiency, 6) == round(ac.grid_charge_efficiency, 6)
    # Der Weg aus dem Netz ist von der Kopplung unberührt.
    assert dc.grid_charge_efficiency == ac.grid_charge_efficiency


def test_netzladeleistung_folgt_dem_typenschild():
    battery = Battery(charger_count=3, charger_current_a=70.0, nominal_voltage_v=51.2)
    assert round(battery.grid_charge_kw, 2) == 10.75
    assert Battery(grid_charge_kw_override=4.0).grid_charge_kw == 4.0


def test_ac_gekoppelte_pv_teilt_sich_die_ladegeraete():
    battery = Battery(pv_coupling="ac", mppt_charge_kw=30.0)
    assert battery.charge_kw == battery.grid_charge_kw
    assert battery.pv_charge_kw == battery.grid_charge_kw
    assert battery.pv_charge_efficiency == battery.grid_charge_efficiency


def test_dc_kopplung_addiert_die_beiden_ladewege():
    battery = Battery(pv_coupling="dc", mppt_charge_kw=8.0, grid_charge_kw_override=4.0)
    assert battery.charge_kw == 12.0     # beide Wege können gleichzeitig liefern
    assert battery.pv_charge_kw == 8.0


def test_szenario_verdoppelt_nur_die_kapazitaet():
    settings = Settings()
    settings.scenarios.battery_60kwh = True
    scenario = settings.scenario_battery()
    assert scenario.nominal_kwh == 60.0
    assert scenario.charge_kw == settings.battery.charge_kw
    assert settings.battery.nominal_kwh == 30.0  # Original bleibt unveraendert


def test_einstellungen_akzeptieren_strings_aus_dem_formular():
    settings = Settings.from_dict(
        {
            "battery": {"nominal_kwh": "40,5", "grid_charge_controllable": "true"},
            "ev": {"window_start_hour": "22"},
            "unbekannt": {"egal": 1},
        }
    )
    assert settings.battery.nominal_kwh == 40.5
    assert settings.battery.grid_charge_controllable is True
    assert settings.ev.window_start_hour == 22


def test_speichern_und_laden_ist_verlustfrei(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    settings = Settings()
    settings.configured = True
    settings.entities.grid_import = "sensor.netzbezug"
    settings.forecast.monthly_reference_kwh["9"] = 188.0
    store.save(settings)

    loaded = store.load()
    assert loaded.configured is True
    assert loaded.entities.grid_import == "sensor.netzbezug"
    assert loaded.forecast.monthly_reference_kwh["9"] == 188.0
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["configured"] is True


def test_fehlende_datei_ergibt_standardwerte(tmp_path):
    assert SettingsStore(tmp_path / "fehlt.json").load().configured is False
