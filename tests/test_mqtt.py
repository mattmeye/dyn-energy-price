"""MQTT-Discovery: Nutzlasten, Verfügbarkeit und Auswahl des Übertragungswegs."""

from __future__ import annotations

import json

from conftest import DAY, TZ, flat_history, night_cheap_prices, pv_state

from nsforecast.config import AddonOptions, Settings, SettingsStore
from nsforecast.evaluate import evaluate_day
from nsforecast.forecast import HistoryBundle, build_day_forecast
from nsforecast.mqttpublish import (
    OFFLINE,
    ONLINE,
    STATUS_TOPIC,
    BrokerInfo,
    discovery_payload,
    messages,
    topics,
)
from nsforecast.prices import PriceProvider
from nsforecast.publish import build_sensors
from nsforecast.runner import Runner
from nsforecast.store import Store
from nsforecast.timeutil import slot_starts_utc


def make_evaluation(settings, prices=None):
    slots = slot_starts_utc(DAY, TZ)
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(3.0))
    forecast = build_day_forecast(DAY, slots, TZ, settings, bundle)
    return evaluate_day(forecast, prices or night_cheap_prices(slots), settings, TZ,
                        settings.scenario_battery(), None, start_energy_kwh=0.0)


def test_discovery_topics_folgen_der_konvention(settings):
    sensor = build_sensors(make_evaluation(settings))[0]
    t = topics(sensor)
    assert t["config"] == "homeassistant/sensor/naturstrom_smart/ersparnis_folgetag/config"
    assert t["state"] == "naturstrom_smart/ersparnis_folgetag/state"


def test_discovery_nutzlast_traegt_geraet_und_kennung(settings):
    sensor = build_sensors(make_evaluation(settings))[0]
    payload = discovery_payload(sensor)
    assert payload["unique_id"] == "naturstrom_smart_ersparnis_folgetag"
    assert payload["device"]["identifiers"] == ["naturstrom_smart"]
    assert payload["unit_of_measurement"] == "EUR"
    assert payload["availability_mode"] == "all"
    assert {entry["topic"] for entry in payload["availability"]} == {
        STATUS_TOPIC, "naturstrom_smart/ersparnis_folgetag/availability"
    }


def test_binaersensor_bekommt_eigene_domaene(settings):
    sensoren = {s.key: s for s in build_sensors(make_evaluation(settings))}
    binaer = sensoren["guenstiger_als_fixtarif"]
    assert binaer.domain == "binary_sensor"
    assert topics(binaer)["config"].startswith("homeassistant/binary_sensor/")


def test_alle_nachrichten_sind_dauerhaft(settings):
    for topic, payload, retain in messages(make_evaluation(settings)):
        assert retain is True, topic
        assert isinstance(payload, str)


def test_attribute_sind_gueltiges_json(settings):
    for topic, payload, _ in messages(make_evaluation(settings)):
        if topic.endswith("/attributes") or topic.endswith("/config"):
            json.loads(payload)


def test_fehlender_wert_wird_als_unverfuegbar_gemeldet(settings):
    """Ohne Ladefenster gibt es keinen Zeitstempel - dann lieber nicht verfügbar."""
    ohne_fenster = make_evaluation(settings, prices=[95.0] * 96)
    gesendet = dict((topic, payload) for topic, payload, _ in messages(ohne_fenster))
    assert gesendet["naturstrom_smart/ladefenster_start/availability"] == OFFLINE
    assert "naturstrom_smart/ladefenster_start/state" not in gesendet
    # Werte, die es immer gibt, bleiben verfügbar.
    assert gesendet["naturstrom_smart/tagespreis/availability"] == ONLINE


def test_vorhandener_wert_wird_gesendet(settings):
    gesendet = dict((topic, payload) for topic, payload, _ in messages(make_evaluation(settings)))
    assert gesendet["naturstrom_smart/ladefenster_start/availability"] == ONLINE
    assert gesendet["naturstrom_smart/ladefenster_start/state"].endswith("Z")


def test_jede_entitaet_bekommt_eine_discovery_nachricht(settings):
    evaluation = make_evaluation(settings)
    configs = [t for t, _, _ in messages(evaluation) if t.endswith("/config")]
    assert len(configs) == len(build_sensors(evaluation))


class FakeHass:
    configured = True
    token = "abc"

    def __init__(self):
        self.states = {}

    def ping(self):
        return True

    def set_state(self, entity_id, state, attributes=None):
        self.states[entity_id] = state

    def statistics(self, *args, **kwargs):
        return {}

    def state(self, entity_id):
        return None


def make_runner(tmp_path, settings):
    settings_store = SettingsStore(tmp_path / "settings.json")
    settings_store.save(settings)
    return Runner(
        options=AddonOptions(data_dir=tmp_path),
        settings_store=settings_store,
        store=Store(tmp_path / "db.sqlite"),
        hass=FakeHass(),
        prices=PriceProvider(tmp_path / "cache", offline=True),
    )


def test_ohne_broker_wird_die_zustands_api_genutzt(tmp_path, settings):
    runner = make_runner(tmp_path, settings)
    runner._broker = None
    anzahl, weg = runner.publish_result(settings, make_evaluation(settings))
    assert weg == "Zustands-API"
    assert anzahl == len(build_sensors(make_evaluation(settings)))
    assert "sensor.naturstrom_smart_ersparnis_folgetag" in runner.hass.states


def test_mit_broker_wird_mqtt_bevorzugt(tmp_path, settings, monkeypatch):
    runner = make_runner(tmp_path, settings)
    runner._broker = BrokerInfo(host="core-mosquitto", port=1883)
    gesendet = {}

    def fake_publish(self, evaluation, timeout=15.0):
        gesendet["anzahl"] = len(messages(evaluation))
        return gesendet["anzahl"]

    monkeypatch.setattr("nsforecast.mqttpublish.MqttPublisher.publish", fake_publish)
    anzahl, weg = runner.publish_result(settings, make_evaluation(settings))
    assert weg == "MQTT"
    assert anzahl == gesendet["anzahl"]
    assert runner.hass.states == {}   # die Zustands-API bleibt unberührt


def test_erzwungenes_mqtt_faellt_nicht_zurueck(tmp_path, settings):
    settings.sensor_mode = "mqtt"
    runner = make_runner(tmp_path, settings)
    runner._broker = None
    anzahl, weg = runner.publish_result(settings, make_evaluation(settings))
    assert anzahl == 0
    assert "MQTT" in weg
    assert runner.hass.states == {}


def test_erzwungene_zustands_api_ignoriert_den_broker(tmp_path, settings):
    settings.sensor_mode = "rest"
    runner = make_runner(tmp_path, settings)
    runner._broker = BrokerInfo(host="core-mosquitto", port=1883)
    _, weg = runner.publish_result(settings, make_evaluation(settings))
    assert weg == "Zustands-API"


def test_mqtt_fehler_faellt_auf_die_zustands_api_zurueck(tmp_path, settings, monkeypatch):
    runner = make_runner(tmp_path, settings)
    runner._broker = BrokerInfo(host="core-mosquitto", port=1883)

    def kaputt(self, evaluation, timeout=15.0):
        raise OSError("Broker nicht erreichbar")

    monkeypatch.setattr("nsforecast.mqttpublish.MqttPublisher.publish", kaputt)
    anzahl, weg = runner.publish_result(settings, make_evaluation(settings))
    assert weg == "Zustands-API"
    assert anzahl > 0
