"""Aufbereitung der Home-Assistant-Zustaende."""

from __future__ import annotations

from conftest import DAY, TZ, flat_history, night_cheap_prices, pv_state

from nsforecast.evaluate import evaluate_day
from nsforecast.forecast import HistoryBundle, build_day_forecast
from nsforecast.hass import HomeAssistantError
from nsforecast.publish import build_states, publish
from nsforecast.timeutil import slot_starts_utc


def make_evaluation(settings, prices=None, start_energy=0.0):
    slots = slot_starts_utc(DAY, TZ)
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(3.0))
    forecast = build_day_forecast(DAY, slots, TZ, settings, bundle)
    return evaluate_day(forecast, prices or night_cheap_prices(slots), settings, TZ,
                        settings.scenario_battery(), None, start_energy_kwh=start_energy)


class FakeHass:
    def __init__(self, fail_for=()):
        self.written = {}
        self.fail_for = set(fail_for)
        self.configured = True

    def set_state(self, entity_id, state, attributes=None):
        if entity_id in self.fail_for:
            raise HomeAssistantError("abgelehnt")
        self.written[entity_id] = (state, attributes or {})


def test_alle_erwarteten_entitaeten_entstehen(settings):
    states = dict((entity, (value, attrs)) for entity, value, attrs in build_states(make_evaluation(settings)))
    erwartet = {
        "sensor.naturstrom_smart_ersparnis_folgetag",
        "sensor.naturstrom_smart_ladefenster_start",
        "sensor.naturstrom_smart_ladefenster_ende",
        "sensor.naturstrom_smart_ladefenster_preis",
        "sensor.naturstrom_smart_tagespreis",
        "sensor.naturstrom_smart_netzbezug_prognose",
        "sensor.naturstrom_smart_verschiebbare_energie",
        "sensor.naturstrom_smart_speicherstatus",
        "binary_sensor.naturstrom_smart_guenstiger_als_fixtarif",
    }
    assert erwartet <= set(states)


def test_speicherstatus_traegt_gruende(settings):
    settings.battery.nominal_kwh = 5.0
    states = {entity: (value, attrs) for entity, value, attrs in build_states(make_evaluation(settings))}
    value, attributes = states["sensor.naturstrom_smart_speicherstatus"]
    assert value == "limitiert"
    assert attributes["verdikt"] == "teilweise"
    assert attributes["gruende"]
    assert "kapazitaet" in attributes["begrenzt_durch"]


def test_binaersensor_zeigt_den_vergleich(settings):
    guenstig = {e: v for e, v, _ in build_states(make_evaluation(settings))}
    assert guenstig["binary_sensor.naturstrom_smart_guenstiger_als_fixtarif"] == "on"

    settings.tariff.fixed_price_ct = 15.0
    teuer = {e: v for e, v, _ in build_states(make_evaluation(settings, prices=[95.0] * 96))}
    assert teuer["binary_sensor.naturstrom_smart_guenstiger_als_fixtarif"] == "off"


def test_zeitstempel_sind_iso_in_utc(settings):
    states = {e: v for e, v, _ in build_states(make_evaluation(settings))}
    assert states["sensor.naturstrom_smart_ladefenster_start"].endswith("Z")


def test_ohne_fenster_bleibt_der_zeitstempel_unbekannt(settings):
    states = {e: v for e, v, _ in build_states(make_evaluation(settings, prices=[95.0] * 96))}
    assert states["sensor.naturstrom_smart_ladefenster_start"] == "unknown"


def test_publish_zaehlt_erfolge_und_haelt_fehler_aus(settings):
    evaluation = make_evaluation(settings)
    hass = FakeHass()
    assert publish(hass, evaluation) == len(build_states(evaluation))

    mit_fehler = FakeHass(fail_for={"sensor.naturstrom_smart_tagespreis"})
    assert publish(mit_fehler, evaluation) == len(build_states(evaluation)) - 1
