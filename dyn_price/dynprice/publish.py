"""Ergebnisse als Home-Assistant-Entitäten beschreiben und über die Zustands-API setzen.

Die Definition der Entitäten steht hier einmal; wie sie zu Home Assistant kommen,
entscheiden die Backends: MQTT-Discovery (dauerhaft, im Geräteregister) oder die
Zustands-API (ohne Zusatzdienst, aber flüchtig).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .hass import HomeAssistant, HomeAssistantError

_LOG = logging.getLogger(__name__)

PREFIX = "dyn_price"
DEVICE_NAME = "Dynamischer Strompreis"

VERDICT_TEXT = {
    "verschiebbar": "ausreichend",
    "teilweise": "limitiert",
    "nicht_verschiebbar": "nicht ausreichend",
    "nicht_noetig": "nicht nötig",
}


@dataclass(frozen=True)
class SensorSpec:
    """Eine Entität, unabhängig vom Übertragungsweg beschrieben."""

    key: str
    name: str
    state: str | None                 # None = Wert unbekannt
    domain: str = "sensor"
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def entity_id(self) -> str:
        return f"{self.domain}.{PREFIX}_{self.key}"

    @property
    def unique_id(self) -> str:
        return f"{PREFIX}_{self.key}"


def build_sensors(payload: dict[str, Any]) -> list[SensorSpec]:
    """Alle Entitäten für ein abgelegtes Tagesergebnis.

    Arbeitet auf dem gespeicherten JSON, damit sich ein Ergebnis auch ohne
    Neuberechnung erneut veröffentlichen lässt - etwa nach einem Neustart.
    """
    storage = payload.get("storage", {})
    savings = payload.get("savings", {})
    costs = payload.get("costs", {})
    prices = payload.get("prices", {})
    energy = payload.get("energy", {})
    window = payload.get("window", {})

    gemeinsam = {
        "prognose_fuer": payload.get("day"),
        "erstellt": payload.get("generated_at"),
        "szenario": payload.get("scenario"),
    }
    import_kwh = float(energy.get("import_kwh", 0.0))
    fixtarif_ct = (
        round(float(costs.get("fixed_eur", 0.0)) * 100.0 / import_kwh, 2)
        if import_kwh > 1e-9
        else None
    )
    ersparnis = float(savings.get("vs_fixed_eur", 0.0))
    fenster_start = window.get("start_utc")

    return [
        SensorSpec(
            key="ersparnis_folgetag",
            name="Ersparnis Folgetag",
            state=f"{ersparnis:.2f}",
            unit="EUR",
            state_class="measurement",
            icon="mdi:cash",
            attributes={
                **gemeinsam,
                "ohne_verschiebung_eur": savings.get("vs_fixed_unshifted_eur"),
                "ohne_speichergrenzen_eur": savings.get("vs_fixed_ideal_eur"),
                "grundpreisanteil_eur": costs.get("base_price_delta_eur"),
                "kosten_dynamisch_eur": costs.get("dynamic_shifted_eur"),
                "kosten_fixtarif_eur": costs.get("fixed_eur"),
            },
        ),
        SensorSpec(
            key="ladefenster_start",
            name="Ladefenster Start",
            state=fenster_start,
            device_class="timestamp",
            icon="mdi:clock-start",
            attributes=dict(gemeinsam),
        ),
        SensorSpec(
            key="ladefenster_ende",
            name="Ladefenster Ende",
            state=window.get("end_utc"),
            device_class="timestamp",
            icon="mdi:clock-end",
            attributes=dict(gemeinsam),
        ),
        SensorSpec(
            key="ladefenster_preis",
            name="Ladefenster Preis",
            state=f"{float(window.get('avg_price_ct', 0.0)):.2f}" if fenster_start else None,
            unit="ct/kWh",
            state_class="measurement",
            icon="mdi:tag-arrow-down",
            attributes=dict(gemeinsam),
        ),
        SensorSpec(
            key="tagespreis",
            name="Tagespreis ohne Verschiebung",
            state=f"{float(prices.get('day_avg_ct', 0.0)):.2f}",
            unit="ct/kWh",
            state_class="measurement",
            icon="mdi:tag",
            attributes={
                **gemeinsam,
                "guenstigster_slot_ct": prices.get("cheapest_slot_ct"),
                "fixtarif_ct": fixtarif_ct,
            },
        ),
        SensorSpec(
            key="netzbezug_prognose",
            name="Netzbezug Prognose",
            state=f"{import_kwh:.2f}",
            unit="kWh",
            state_class="measurement",
            icon="mdi:transmission-tower-import",
            attributes={**gemeinsam, "pv_prognose_kwh": energy.get("pv_kwh")},
        ),
        SensorSpec(
            key="verschiebbare_energie",
            name="Verschiebbare Energie",
            state=f"{float(storage.get('shifted_kwh', 0.0)):.2f}",
            unit="kWh",
            state_class="measurement",
            icon="mdi:swap-horizontal",
            attributes={
                **gemeinsam,
                "bedarf_kwh": storage.get("needed_kwh"),
                "nicht_verschiebbar_kwh": storage.get("not_shifted_kwh"),
            },
        ),
        SensorSpec(
            key="speicherstatus",
            name="Speicherstatus",
            state=VERDICT_TEXT.get(storage.get("verdict", ""), storage.get("verdict")),
            icon="mdi:battery-check",
            attributes={
                **gemeinsam,
                "verdikt": storage.get("verdict"),
                "gruende": storage.get("reasons", []),
                "begrenzt_durch": storage.get("binding", []),
                "freie_kapazitaet_kwh": storage.get("capacity_available_kwh"),
                "benoetigte_ladung_kwh": storage.get("capacity_needed_kwh"),
                "ungenutzte_kapazitaet_kwh": storage.get("unused_capacity_kwh"),
                "pv_reserviert_kwh": storage.get("pv_reserved_kwh"),
                "netzladeleistung_kw": storage.get("charge_power_available_kw"),
            },
        ),
        SensorSpec(
            key="guenstiger_als_fixtarif",
            name="Günstiger als Fixtarif",
            domain="binary_sensor",
            state="on" if ersparnis >= 0 else "off",
            icon="mdi:scale-balance",
            attributes={
                **gemeinsam,
                "ersparnis_eur": round(ersparnis, 2),
                "warnungen": payload.get("warnings", []),
            },
        ),
    ]


def rest_attributes(sensor: SensorSpec) -> dict[str, Any]:
    """Attribute für die Zustands-API; Einheit und Klassen gehören dort hinein."""
    attributes: dict[str, Any] = {"friendly_name": f"Strompreis {sensor.name}"}
    if sensor.unit:
        attributes["unit_of_measurement"] = sensor.unit
    if sensor.device_class:
        attributes["device_class"] = sensor.device_class
    if sensor.state_class:
        attributes["state_class"] = sensor.state_class
    if sensor.icon:
        attributes["icon"] = sensor.icon
    attributes.update(sensor.attributes)
    return attributes


def publish(hass: HomeAssistant, payload: dict[str, Any]) -> int:
    """Über die Zustands-API setzen. Die Entitäten überleben keinen HA-Neustart."""
    written = 0
    for sensor in build_sensors(payload):
        try:
            hass.set_state(
                sensor.entity_id,
                sensor.state if sensor.state is not None else "unknown",
                rest_attributes(sensor),
            )
            written += 1
        except HomeAssistantError as err:
            _LOG.warning("Konnte %s nicht setzen: %s", sensor.entity_id, err)
    return written


def build_states(payload: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Entitäten in der Form der Zustands-API."""
    return [
        (s.entity_id, s.state if s.state is not None else "unknown", rest_attributes(s))
        for s in build_sensors(payload)
    ]
