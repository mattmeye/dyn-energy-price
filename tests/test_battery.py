"""Speichersimulation: Energiebilanz, Grenzen, PV-Vorrang."""

from __future__ import annotations

from nsforecast.battery import classify, pv_reserve_after, simulate_baseline
from nsforecast.config import Battery


def battery(**kwargs) -> Battery:
    """Speicher mit runden Werten; Wandlerverluste stecken in den drei Stufen."""
    defaults = dict(nominal_kwh=30.0, soc_min_pct=10.0, soc_max_pct=100.0,
                    mppt_charge_kw=10.0, grid_charge_kw_override=10.0, discharge_kw=10.0,
                    battery_dc_efficiency=0.9, charger_efficiency=1.0, inverter_efficiency=1.0)
    defaults.update(kwargs)
    return Battery(**defaults)


def test_energiebilanz_geht_auf():
    load = [0.5] * 96
    pv = [0.0] * 32 + [1.0] * 40 + [0.0] * 24
    result = simulate_baseline(load, pv, battery(), start_energy_kwh=5.0, duration_h=0.25)
    gedeckt = sum(result.pv_to_load_kwh) + sum(result.battery_discharge_kwh) + result.total_import_kwh
    assert round(gedeckt, 9) == round(sum(load), 9)


def test_pv_bilanz_geht_auf():
    load = [0.2] * 96
    pv = [0.0] * 32 + [1.5] * 40 + [0.0] * 24
    result = simulate_baseline(load, pv, battery(), 0.0, 0.25)
    verteilt = sum(result.pv_to_load_kwh) + sum(result.pv_to_battery_kwh) + sum(result.pv_export_kwh)
    assert round(verteilt, 9) == round(sum(pv), 9)


def test_ladeleistung_wird_eingehalten():
    """Bei DC-Kopplung begrenzen die MPPT-Regler den PV-Weg."""
    speicher = battery(pv_coupling="dc", mppt_charge_kw=4.0)
    result = simulate_baseline([0.0] * 96, [5.0] * 96, speicher, 0.0, 0.25)
    assert max(result.pv_to_battery_kwh) <= 4.0 * 0.25 + 1e-9


def test_bei_ac_kopplung_begrenzen_die_ladegeraete_den_pv_weg():
    speicher = battery(pv_coupling="ac", mppt_charge_kw=30.0, grid_charge_kw_override=4.0)
    result = simulate_baseline([0.0] * 96, [5.0] * 96, speicher, 0.0, 0.25)
    assert max(result.pv_to_battery_kwh) <= 4.0 * 0.25 + 1e-9


def test_entladeleistung_begrenzt_die_deckung():
    result = simulate_baseline([5.0] * 96, [0.0] * 96, battery(discharge_kw=6.0), 27.0, 0.25)
    assert max(result.battery_discharge_kwh) <= 6.0 * 0.25 + 1e-9
    assert result.total_import_kwh > 0


def test_speicher_ueberschreitet_die_kapazitaet_nicht():
    result = simulate_baseline([0.0] * 96, [10.0] * 96, battery(), 0.0, 0.25)
    assert max(result.soc_kwh) <= battery().usable_kwh + 1e-9
    assert sum(result.pv_export_kwh) > 0


def test_voller_speicher_speist_ein():
    result = simulate_baseline([0.0] * 8, [4.0] * 8, battery(), 27.0, 0.25)
    assert round(sum(result.pv_export_kwh), 6) == round(sum([4.0] * 8), 6)


def test_wirkungsgrad_kostet_energie():
    verlustfrei = simulate_baseline([0.0] * 4 + [1.0] * 4, [1.0] * 4 + [0.0] * 4,
                                    battery(battery_dc_efficiency=1.0), 0.0, 0.25)
    mit_verlust = simulate_baseline([0.0] * 4 + [1.0] * 4, [1.0] * 4 + [0.0] * 4,
                                    battery(battery_dc_efficiency=0.8), 0.0, 0.25)
    assert mit_verlust.total_import_kwh > verlustfrei.total_import_kwh


def test_pv_reserve_beruecksichtigt_nur_die_zeit_danach():
    surplus = [0.0] * 48 + [0.3] * 48
    voll = pv_reserve_after(surplus, 0, battery(), 0.25)
    danach = pv_reserve_after(surplus, 60, battery(), 0.25)
    assert voll > danach > 0
    assert pv_reserve_after(surplus, 96, battery(), 0.25) == 0.0
    assert voll <= battery().usable_kwh


def test_pv_reserve_wird_auf_die_kapazitaet_gedeckelt():
    assert pv_reserve_after([2.0] * 96, 0, battery(), 0.25) == battery().usable_kwh


def test_verdikt_stufen():
    assert classify(0.0, 0.0) == "nicht_noetig"
    assert classify(10.0, 10.0) == "verschiebbar"
    assert classify(10.0, 9.9) == "verschiebbar"      # innerhalb der Toleranz
    assert classify(10.0, 5.0) == "teilweise"
    assert classify(10.0, 0.0) == "nicht_verschiebbar"
