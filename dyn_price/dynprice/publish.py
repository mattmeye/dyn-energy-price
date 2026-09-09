"""Ergebnisse als Home-Assistant-Entitäten beschreiben und über die Zustands-API setzen.

Die Definition der Entitäten steht hier einmal; wie sie zu Home Assistant kommen,
entscheiden die Backends: MQTT-Discovery (dauerhaft, im Geräteregister) oder die
Zustands-API (ohne Zusatzdienst, aber flüchtig).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .evaluate import DayEvaluation
from .hass import HomeAssistant, HomeAssistantError
from .timeutil import iso_utc

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


def build_sensors(evaluation: DayEvaluation) -> list[SensorSpec]:
    """Alle Entitäten für ein Tagesergebnis."""
    storage = evaluation.storage
    gemeinsam = {
        "prognose_fuer": evaluation.day.isoformat(),
        "erstellt": iso_utc(evaluation.generated_at),
        "szenario": evaluation.scenario,
    }
    fixtarif_ct = (
        round(evaluation.cost_fixed_eur * 100.0 / evaluation.total_import_kwh, 2)
        if evaluation.total_import_kwh > 1e-9
        else None
    )

    return [
        SensorSpec(
            key="ersparnis_folgetag",
            name="Ersparnis Folgetag",
            state=f"{evaluation.saving_vs_fixed_eur:.2f}",
            unit="EUR",
            state_class="measurement",
            icon="mdi:cash",
            attributes={
                **gemeinsam,
                "ohne_verschiebung_eur": round(evaluation.saving_vs_fixed_unshifted_eur, 2),
                "ohne_speichergrenzen_eur": round(evaluation.saving_vs_fixed_ideal_eur, 2),
                "grundpreisanteil_eur": round(evaluation.base_price_delta_eur, 2),
                "kosten_dynamisch_eur": round(evaluation.cost_dynamic_shifted_eur, 2),
                "kosten_fixtarif_eur": round(evaluation.cost_fixed_eur, 2),
            },
        ),
        SensorSpec(
            key="ladefenster_start",
            name="Ladefenster Start",
            state=iso_utc(evaluation.window_start_utc) if evaluation.window_start_utc else None,
            device_class="timestamp",
            icon="mdi:clock-start",
            attributes=dict(gemeinsam),
        ),
        SensorSpec(
            key="ladefenster_ende",
            name="Ladefenster Ende",
            state=iso_utc(evaluation.window_end_utc) if evaluation.window_end_utc else None,
            device_class="timestamp",
            icon="mdi:clock-end",
            attributes=dict(gemeinsam),
        ),
        SensorSpec(
            key="ladefenster_preis",
            name="Ladefenster Preis",
            state=f"{evaluation.window_avg_price_ct:.2f}" if evaluation.window_start_utc else None,
            unit="ct/kWh",
            state_class="measurement",
            icon="mdi:tag-arrow-down",
            attributes=dict(gemeinsam),
        ),
        SensorSpec(
            key="tagespreis",
            name="Tagespreis ohne Verschiebung",
            state=f"{evaluation.day_avg_price_ct:.2f}",
            unit="ct/kWh",
            state_class="measurement",
            icon="mdi:tag",
            attributes={
                **gemeinsam,
                "guenstigster_slot_ct": round(evaluation.cheapest_slot_price_ct, 2),
                "fixtarif_ct": fixtarif_ct,
            },
        ),
        SensorSpec(
            key="netzbezug_prognose",
            name="Netzbezug Prognose",
            state=f"{evaluation.total_import_kwh:.2f}",
            unit="kWh",
            state_class="measurement",
            icon="mdi:transmission-tower-import",
            attributes={**gemeinsam, "pv_prognose_kwh": round(evaluation.total_pv_kwh, 2)},
        ),
        SensorSpec(
            key="verschiebbare_energie",
            name="Verschiebbare Energie",
            state=f"{storage.shifted_kwh:.2f}",
            unit="kWh",
            state_class="measurement",
            icon="mdi:swap-horizontal",
            attributes={
                **gemeinsam,
                "bedarf_kwh": round(storage.needed_kwh, 2),
                "nicht_verschiebbar_kwh": round(storage.not_shifted_kwh, 2),
            },
        ),
        SensorSpec(
            key="speicherstatus",
            name="Speicherstatus",
            state=VERDICT_TEXT.get(storage.verdict, storage.verdict),
            icon="mdi:battery-check",
            attributes={
                **gemeinsam,
                "verdikt": storage.verdict,
                "gruende": storage.reasons,
                "begrenzt_durch": storage.binding,
                "freie_kapazitaet_kwh": round(storage.capacity_available_kwh, 2),
                "benoetigte_ladung_kwh": round(storage.capacity_needed_kwh, 2),
                "ungenutzte_kapazitaet_kwh": round(storage.unused_capacity_kwh, 2),
                "pv_reserviert_kwh": round(storage.pv_reserved_kwh, 2),
                "netzladeleistung_kw": round(storage.charge_power_available_kw, 2),
            },
        ),
        SensorSpec(
            key="guenstiger_als_fixtarif",
            name="Günstiger als Fixtarif",
            domain="binary_sensor",
            state="on" if evaluation.saving_vs_fixed_eur >= 0 else "off",
            icon="mdi:scale-balance",
            attributes={
                **gemeinsam,
                "ersparnis_eur": round(evaluation.saving_vs_fixed_eur, 2),
                "warnungen": evaluation.warnings,
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


def publish(hass: HomeAssistant, evaluation: DayEvaluation) -> int:
    """Über die Zustands-API setzen. Die Entitäten überleben keinen HA-Neustart."""
    written = 0
    for sensor in build_sensors(evaluation):
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


# Rückwärtskompatibel für Aufrufer, die die alte Form erwarten.
def build_states(evaluation: DayEvaluation) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (s.entity_id, s.state if s.state is not None else "unknown", rest_attributes(s))
        for s in build_sensors(evaluation)
    ]
