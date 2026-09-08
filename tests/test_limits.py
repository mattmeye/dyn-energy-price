"""Auflösung der Lade- und Entladegrenzen aus Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nsforecast.config import Battery, Entities
from nsforecast.hass import EntityState, StatPoint
from nsforecast.limits import apply, power_kw, resolve


def sensor(entity_id, value, unit):
    return EntityState(entity_id, str(value), {"unit_of_measurement": unit})


class FakeHass:
    """Liefert Zustände und optional Minima aus der Langzeitstatistik."""

    configured = True

    def __init__(self, states=None, minima=None):
        self.states = states or {}
        self.minima = minima or {}

    def state(self, entity_id):
        return self.states.get(entity_id)

    def statistics(self, entity_ids, start, end, period="hour"):
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        out = {}
        for entity_id in entity_ids:
            if entity_id in self.minima:
                out[entity_id] = [
                    StatPoint(base + timedelta(hours=i), None, minimum=self.minima[entity_id])
                    for i in range(3)
                ]
        return out


def test_ampere_wird_mit_der_batteriespannung_umgerechnet():
    assert round(power_kw(sensor("s.ccl", 70, "A"), 51.2), 3) == 3.584
    assert power_kw(sensor("s.ccl", 3600, "W"), 51.2) == 3.6
    assert power_kw(sensor("s.ccl", 3.6, "kW"), 51.2) == 3.6


def test_unbekannte_einheit_ergibt_nichts():
    assert power_kw(sensor("s.ccl", 50, "%"), 51.2) is None
    assert power_kw(None, 51.2) is None
    assert power_kw(sensor("s.ccl", "unavailable", "A"), 51.2) is None


def test_ohne_entitaeten_gelten_die_festen_werte():
    limits = resolve(FakeHass(), Battery())
    assert limits.charge_kw == 10.0
    assert limits.grid_charge_kw == 3.6
    assert limits.discharge_kw == 10.0
    assert limits.sources["Ladeleistung"] == "fest eingestellt"


def test_dvcc_grenzen_in_ampere_werden_uebernommen():
    entities = Entities(
        battery_charge_limit="sensor.ccl",
        battery_discharge_limit="sensor.dcl",
        battery_voltage="sensor.u_batt",
    )
    hass = FakeHass({
        "sensor.ccl": sensor("sensor.ccl", 200, "A"),
        "sensor.dcl": sensor("sensor.dcl", 250, "A"),
        "sensor.u_batt": sensor("sensor.u_batt", 52.0, "V"),
    })
    limits = resolve(hass, Battery(), entities)
    assert round(limits.charge_kw, 2) == 10.40
    assert round(limits.discharge_kw, 2) == 13.00
    assert limits.battery_voltage_v == 52.0
    assert "sensor.ccl" in limits.sources["Ladeleistung"]


def test_ersatzspannung_wenn_der_sensor_fehlt():
    entities = Entities(battery_charge_limit="sensor.ccl", battery_voltage="sensor.fehlt")
    limits = resolve(FakeHass({"sensor.ccl": sensor("sensor.ccl", 100, "A")}), Battery(), entities)
    assert limits.battery_voltage_v == 51.2
    assert any("Batteriespannung" in note for note in limits.notes)


def test_eingangsstrom_begrenzt_die_netzladung():
    battery = Battery(grid_charge_kw=5.0, ac_input_limit_a=16.0, mains_voltage_v=230.0, phases=1)
    limits = resolve(FakeHass(), battery)
    assert round(limits.grid_charge_kw, 2) == 3.68
    assert any("Eingangsstromgrenze" in note for note in limits.notes)


def test_dreiphasiger_anschluss_hebt_die_grenze():
    battery = Battery(charge_kw=20.0, grid_charge_kw=20.0, ac_input_limit_a=16.0, phases=3)
    limits = resolve(FakeHass(), battery)
    assert round(limits.grid_charge_kw, 1) == 11.0


def test_netzladung_nie_ueber_der_ladeannahme():
    battery = Battery(charge_kw=3.0, grid_charge_kw=10.0)
    limits = resolve(FakeHass(), battery)
    assert limits.grid_charge_kw == 3.0
    assert any("Ladeannahme" in note for note in limits.notes)


def test_abgesenkte_grenze_der_letzten_tage_wird_gemeldet():
    entities = Entities(battery_charge_limit="sensor.ccl")
    hass = FakeHass({"sensor.ccl": sensor("sensor.ccl", 200, "A")}, {"sensor.ccl": 40.0})
    limits = resolve(hass, Battery(), entities)
    assert any("BMS" in note for note in limits.notes)


def test_stabile_grenze_erzeugt_keinen_hinweis():
    entities = Entities(battery_charge_limit="sensor.ccl")
    hass = FakeHass({"sensor.ccl": sensor("sensor.ccl", 200, "A")}, {"sensor.ccl": 195.0})
    assert not resolve(hass, Battery(), entities).notes


def test_mindest_soc_aus_der_entitaet():
    battery = Battery()
    entities = Entities(battery_soc_min="number.ess_min_soc")
    hass = FakeHass({"number.ess_min_soc": sensor("number.ess_min_soc", 20, "%")})
    limits = resolve(hass, battery, entities)
    assert limits.soc_min_pct == 20.0
    updated = apply(battery, limits)
    assert updated.soc_min_pct == 20.0
    assert battery.soc_min_pct == 10.0   # das Original bleibt unberührt


def test_apply_uebertraegt_die_leistungen():
    battery = Battery()
    limits = resolve(FakeHass(), Battery(charge_kw=8.0, grid_charge_kw=2.0, discharge_kw=6.0))
    updated = apply(battery, limits)
    assert (updated.charge_kw, updated.grid_charge_kw, updated.discharge_kw) == (8.0, 2.0, 6.0)


def test_unlesbare_entitaet_faellt_auf_den_festen_wert_zurueck():
    entities = Entities(battery_charge_limit="sensor.ccl")
    limits = resolve(FakeHass({"sensor.ccl": sensor("sensor.ccl", 70, "%")}),
                     Battery(charge_kw=9.0), entities)
    assert limits.charge_kw == 9.0
    assert any("nicht auswertbar" in note for note in limits.notes)
