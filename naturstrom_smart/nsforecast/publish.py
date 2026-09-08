"""Ergebnisse als Home-Assistant-Zustände bereitstellen."""

from __future__ import annotations

import logging
from typing import Any

from .evaluate import DayEvaluation
from .hass import HomeAssistant, HomeAssistantError
from .timeutil import iso_utc

_LOG = logging.getLogger(__name__)

PREFIX = "naturstrom_smart"

VERDICT_TEXT = {
    "verschiebbar": "ausreichend",
    "teilweise": "limitiert",
    "nicht_verschiebbar": "nicht ausreichend",
    "nicht_noetig": "nicht nötig",
}


def _entity(name: str, domain: str = "sensor") -> str:
    return f"{domain}.{PREFIX}_{name}"


def build_states(evaluation: DayEvaluation) -> list[tuple[str, str, dict[str, Any]]]:
    """Liste aus Entity-ID, Zustand und Attributen."""
    storage = evaluation.storage
    common = {"prognose_fuer": evaluation.day.isoformat(), "erstellt": iso_utc(evaluation.generated_at)}

    states: list[tuple[str, str, dict[str, Any]]] = [
        (
            _entity("ersparnis_folgetag"),
            f"{evaluation.saving_vs_fixed_eur:.2f}",
            {
                **common,
                "friendly_name": "naturstrom smart Ersparnis Folgetag",
                "unit_of_measurement": "EUR",
                "device_class": "monetary",
                "icon": "mdi:cash",
                "ohne_verschiebung_eur": round(evaluation.saving_vs_fixed_unshifted_eur, 2),
                "ohne_speichergrenzen_eur": round(evaluation.saving_vs_fixed_ideal_eur, 2),
                "grundpreisanteil_eur": round(evaluation.base_price_delta_eur, 2),
            },
        ),
        (
            _entity("ladefenster_start"),
            iso_utc(evaluation.window_start_utc) if evaluation.window_start_utc else "unknown",
            {
                **common,
                "friendly_name": "naturstrom smart Ladefenster Start",
                "device_class": "timestamp",
                "icon": "mdi:clock-start",
            },
        ),
        (
            _entity("ladefenster_ende"),
            iso_utc(evaluation.window_end_utc) if evaluation.window_end_utc else "unknown",
            {
                **common,
                "friendly_name": "naturstrom smart Ladefenster Ende",
                "device_class": "timestamp",
                "icon": "mdi:clock-end",
            },
        ),
        (
            _entity("ladefenster_preis"),
            f"{evaluation.window_avg_price_ct:.2f}",
            {
                **common,
                "friendly_name": "naturstrom smart Ladefenster Preis",
                "unit_of_measurement": "ct/kWh",
                "icon": "mdi:tag-arrow-down",
            },
        ),
        (
            _entity("tagespreis"),
            f"{evaluation.day_avg_price_ct:.2f}",
            {
                **common,
                "friendly_name": "naturstrom smart Tagespreis ohne Verschiebung",
                "unit_of_measurement": "ct/kWh",
                "icon": "mdi:tag",
                "guenstigster_slot_ct": round(evaluation.cheapest_slot_price_ct, 2),
                "fixtarif_ct": round(evaluation.cost_fixed_eur * 100.0 / evaluation.total_import_kwh, 2)
                if evaluation.total_import_kwh > 1e-9
                else None,
            },
        ),
        (
            _entity("netzbezug_prognose"),
            f"{evaluation.total_import_kwh:.2f}",
            {
                **common,
                "friendly_name": "naturstrom smart Netzbezug Prognose",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "icon": "mdi:transmission-tower-import",
                "pv_prognose_kwh": round(evaluation.total_pv_kwh, 2),
            },
        ),
        (
            _entity("verschiebbare_energie"),
            f"{storage.shifted_kwh:.2f}",
            {
                **common,
                "friendly_name": "naturstrom smart verschiebbare Energie",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "icon": "mdi:swap-horizontal",
                "bedarf_kwh": round(storage.needed_kwh, 2),
                "nicht_verschiebbar_kwh": round(storage.not_shifted_kwh, 2),
            },
        ),
        (
            _entity("speicherstatus"),
            VERDICT_TEXT.get(storage.verdict, storage.verdict),
            {
                **common,
                "friendly_name": "naturstrom smart Speicherstatus",
                "icon": "mdi:battery-check",
                "verdikt": storage.verdict,
                "gruende": storage.reasons,
                "begrenzt_durch": storage.binding,
                "freie_kapazitaet_kwh": round(storage.capacity_available_kwh, 2),
                "benoetigte_ladung_kwh": round(storage.capacity_needed_kwh, 2),
                "ungenutzte_kapazitaet_kwh": round(storage.unused_capacity_kwh, 2),
                "pv_reserviert_kwh": round(storage.pv_reserved_kwh, 2),
            },
        ),
        (
            _entity("guenstiger_als_fixtarif", domain="binary_sensor"),
            "on" if evaluation.saving_vs_fixed_eur >= 0 else "off",
            {
                **common,
                "friendly_name": "naturstrom smart günstiger als Fixtarif",
                "icon": "mdi:scale-balance",
                "ersparnis_eur": round(evaluation.saving_vs_fixed_eur, 2),
                "warnungen": evaluation.warnings,
            },
        ),
    ]
    return states


def publish(hass: HomeAssistant, evaluation: DayEvaluation) -> int:
    """Zustände schreiben; gibt die Zahl der erfolgreich gesetzten Entitäten zurück."""
    written = 0
    for entity_id, state, attributes in build_states(evaluation):
        try:
            hass.set_state(entity_id, state, attributes)
            written += 1
        except HomeAssistantError as err:
            _LOG.warning("Konnte %s nicht setzen: %s", entity_id, err)
    return written
