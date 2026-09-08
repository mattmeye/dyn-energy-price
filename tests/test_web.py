"""Endpunkte der Weboberflaeche."""

from __future__ import annotations

import json
import urllib.request
from urllib.error import HTTPError

import pytest
from conftest import DAY, TZ, flat_history, night_cheap_prices, pv_state

from nsforecast.config import AddonOptions, SettingsStore
from nsforecast.evaluate import evaluate_day
from nsforecast.forecast import HistoryBundle, build_day_forecast
from nsforecast.hass import EntityState
from nsforecast.prices import PriceProvider
from nsforecast.runner import Runner
from nsforecast.store import Store
from nsforecast.timeutil import slot_starts_utc
from nsforecast.web import serve


class FakeHass:
    configured = True

    def __init__(self):
        self.states_list = [
            EntityState("sensor.netzbezug", "1234", {
                "friendly_name": "Netzbezug", "unit_of_measurement": "kWh",
                "device_class": "energy", "state_class": "total_increasing"}),
            EntityState("sensor.batterie_ladezustand", "55", {
                "friendly_name": "Batterie Ladezustand", "unit_of_measurement": "%",
                "device_class": "battery"}),
        ]

    def ping(self):
        return True

    def states(self):
        return list(self.states_list)

    def state(self, entity_id):
        return None

    def statistics(self, *args, **kwargs):
        return {}

    def set_state(self, *args, **kwargs):
        return None


@pytest.fixture
def server(tmp_path, settings):
    settings.configured = True
    settings_store = SettingsStore(tmp_path / "settings.json")
    settings_store.save(settings)
    store = Store(tmp_path / "db.sqlite")

    slots = slot_starts_utc(DAY, TZ)
    bundle = HistoryBundle(household_hourly=flat_history(), pv_forecast_state=pv_state(3.0))
    forecast = build_day_forecast(DAY, slots, TZ, settings, bundle)
    evaluation = evaluate_day(forecast, night_cheap_prices(slots), settings, TZ,
                              settings.scenario_battery(), None, start_energy_kwh=0.0)
    store.save_forecast(evaluation)
    store.save_actual(DAY, {"status": "ok", "saving_eur": 1.2, "import_kwh": 22.0,
                            "cost_fixed_eur": 6.8, "cost_smart_eur": 5.2})

    runner = Runner(
        options=AddonOptions(data_dir=tmp_path, port=0),
        settings_store=settings_store,
        store=store,
        hass=FakeHass(),
        prices=PriceProvider(tmp_path / "cache", offline=True),
    )
    httpd = serve(runner, None, 0)
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(base, path):
    with urllib.request.urlopen(f"{base}/{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post(base, path, payload):
    request = urllib.request.Request(
        f"{base}/{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_status(server):
    status = get(server, "api/status")
    assert status["configured"] is True
    assert status["home_assistant"] is True
    assert status["timezone"] == "Europe/Berlin"


def test_startseite_wird_ausgeliefert(server):
    with urllib.request.urlopen(f"{server}/", timeout=5) as response:
        body = response.read().decode("utf-8")
    assert "naturstrom smart" in body
    assert response.headers["Content-Type"].startswith("text/html")


def test_statische_dateien(server):
    for name in ("app.css", "app.js", "charts.js"):
        with urllib.request.urlopen(f"{server}/{name}", timeout=5) as response:
            assert response.status == 200


def test_kein_ausbruch_aus_dem_statikverzeichnis(server):
    with pytest.raises(HTTPError) as fehler:
        urllib.request.urlopen(f"{server}/../../config.yaml", timeout=5)
    assert fehler.value.code == 404


def test_entitaeten_werden_vorgeschlagen(server):
    data = get(server, "api/entities")
    rollen = {role["key"]: role for role in data["roles"]}
    assert rollen["grid_import"]["candidates"][0]["entity_id"] == "sensor.netzbezug"
    assert rollen["battery_soc"]["candidates"][0]["entity_id"] == "sensor.batterie_ladezustand"
    assert any(item["entity_id"] == "sensor.netzbezug" for item in data["entities"])


def test_prognose_wird_geliefert(server):
    data = get(server, f"api/forecast?day={DAY.isoformat()}")
    assert data["status"] == "ok"
    assert data["forecast"]["day"] == DAY.isoformat()
    assert data["actual"]["saving_eur"] == 1.2


def test_unbekannter_tag_meldet_leer(server):
    data = get(server, "api/forecast?day=2001-01-01")
    assert data["status"] == "leer"


def test_verlauf_enthaelt_monate_und_fazit(server):
    data = get(server, "api/history?days=400")
    assert data["days"][0]["day"] == DAY.isoformat()
    assert data["months"][0]["days"] == 1
    assert data["conclusion"]


def test_einstellungen_speichern(server):
    settings = get(server, "api/settings")
    settings["battery"]["nominal_kwh"] = 42.0
    settings["entities"]["grid_import"] = "sensor.netzbezug"
    antwort = post(server, "api/settings", settings)
    assert antwort["status"] == "ok"
    assert get(server, "api/settings")["battery"]["nominal_kwh"] == 42.0


def test_lauf_ohne_preise_meldet_sauber(server):
    antwort = post(server, "api/run", {"day": "2030-01-01"})
    assert antwort["status"] == "keine_preise"
    assert "Offline" in antwort["message"] or "energy-charts" in antwort["message"]


def test_laufprotokoll(server):
    post(server, "api/run", {"day": "2030-01-01"})
    runs = get(server, "api/runs")["runs"]
    assert runs[0]["status"] == "keine_preise"


def test_unbekannte_route(server):
    with pytest.raises(HTTPError) as fehler:
        urllib.request.urlopen(f"{server}/api/gibtsnicht", timeout=5)
    assert fehler.value.code == 404
