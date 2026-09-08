"""Automatische Erkennung der Entitaeten."""

from __future__ import annotations

from nsforecast.discovery import ROLES_BY_KEY, discover, score_entity, suggest
from nsforecast.hass import EntityState


def sensor(entity_id, name, unit, device_class="", state_class="", state="1.0"):
    return EntityState(entity_id, state, {
        "friendly_name": name, "unit_of_measurement": unit,
        "device_class": device_class, "state_class": state_class,
    })


ANLAGE = [
    sensor("sensor.netzbezug_gesamt", "Netzbezug gesamt", "kWh", "energy", "total_increasing", "1234.5"),
    sensor("sensor.einspeisung_gesamt", "Einspeisung gesamt", "kWh", "energy", "total_increasing", "900"),
    sensor("sensor.energy_production_tomorrow", "Geschaetzte Energieproduktion morgen", "kWh", "energy", "total", "18.4"),
    sensor("sensor.energy_production_today", "Geschaetzte Energieproduktion heute", "kWh", "energy", "total", "12.1"),
    sensor("sensor.batterie_ladezustand", "Batterie Ladezustand", "%", "battery", "measurement", "64"),
    sensor("sensor.handy_akku", "Handy Battery", "%", "battery", "measurement", "80"),
    sensor("sensor.wallbox_geladene_energie", "Wallbox geladene Energie", "kWh", "energy", "total_increasing", "540"),
    sensor("sensor.pv_erzeugung_gesamt", "PV Erzeugung gesamt", "kWh", "energy", "total_increasing", "8800"),
    sensor("sensor.strompreis", "Strompreis", "ct/kWh", "", "measurement", "28"),
]


def test_pflichtrollen_werden_erkannt():
    chosen = suggest(ANLAGE)
    assert chosen["grid_import"] == "sensor.netzbezug_gesamt"
    assert chosen["battery_soc"] == "sensor.batterie_ladezustand"
    assert chosen["pv_forecast_tomorrow"] == "sensor.energy_production_tomorrow"


def test_einspeisung_wird_nicht_als_bezug_vorgeschlagen():
    chosen = suggest(ANLAGE)
    assert chosen["grid_export"] == "sensor.einspeisung_gesamt"
    assert chosen["grid_import"] != "sensor.einspeisung_gesamt"


def test_keine_doppelbelegung():
    gewaehlt = [value for value in suggest(ANLAGE).values() if value]
    assert len(gewaehlt) == len(set(gewaehlt))


def test_prognose_heute_und_morgen_werden_getrennt():
    chosen = suggest(ANLAGE)
    assert chosen["pv_forecast_today"] == "sensor.energy_production_today"
    assert chosen["pv_forecast_tomorrow"] == "sensor.energy_production_tomorrow"


def test_falsche_einheit_zaehlt_nicht():
    assert score_entity(sensor("sensor.netzbezug", "Netzbezug", "W", "power"),
                        ROLES_BY_KEY["grid_import"]) == 0


def test_nicht_sensoren_werden_uebergangen():
    assert score_entity(sensor("switch.netzbezug", "Netzbezug", "kWh"),
                        ROLES_BY_KEY["grid_import"]) == 0


def test_nicht_verfuegbare_entitaeten_werden_uebergangen():
    entity = sensor("sensor.netzbezug", "Netzbezug", "kWh", "energy", "total_increasing", "unavailable")
    assert score_entity(entity, ROLES_BY_KEY["grid_import"]) == 0


def test_passende_geraeteklasse_erhoeht_die_punktzahl():
    ohne = sensor("sensor.netzbezug", "Netzbezug", "kWh")
    mit = sensor("sensor.netzbezug", "Netzbezug", "kWh", "energy", "total_increasing")
    assert score_entity(mit, ROLES_BY_KEY["grid_import"]) > score_entity(ohne, ROLES_BY_KEY["grid_import"])


def test_kandidaten_sind_absteigend_sortiert():
    kandidaten = discover(ANLAGE)["grid_import"]
    punkte = [c.score for c in kandidaten]
    assert punkte == sorted(punkte, reverse=True)


def test_leere_anlage_ergibt_leere_vorschlaege():
    assert all(value == "" for value in suggest([]).values())
