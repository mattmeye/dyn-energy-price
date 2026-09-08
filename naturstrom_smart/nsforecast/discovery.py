"""Erkennt passende Home-Assistant-Entitäten für die Rollen des Add-ons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .hass import EntityState

ENERGY_UNITS = {"kwh", "wh", "mwh"}
POWER_UNITS = {"w", "kw"}


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    hint: str
    units: frozenset[str]
    positive: tuple[str, ...]
    negative: tuple[str, ...] = ()
    device_classes: tuple[str, ...] = ()
    state_classes: tuple[str, ...] = ()
    required: bool = False


ROLES: tuple[Role, ...] = (
    Role(
        key="grid_import",
        label="Netzbezug (Zähler)",
        hint="kWh, total_increasing, z. B. IR-Lesekopf am Zähler",
        units=frozenset(ENERGY_UNITS),
        positive=("netzbezug", "grid_import", "grid_consumption", "bezug", "import", "zaehler",
                  "zahler", "meter", "energy_meter", "netz"),
        negative=("einspeis", "export", "feed_in", "feedin", "return", "ruecklauf", "cost", "price",
                  "preis", "solar", "pv_", "battery", "akku"),
        device_classes=("energy",),
        state_classes=("total_increasing", "total"),
        required=True,
    ),
    Role(
        key="grid_export",
        label="Netzeinspeisung",
        hint="optional, verbessert die Trennung von PV-Überschuss",
        units=frozenset(ENERGY_UNITS),
        positive=("einspeis", "export", "feed_in", "feedin", "grid_return", "ruecklauf"),
        negative=("bezug", "import", "cost", "price", "preis"),
        device_classes=("energy",),
        state_classes=("total_increasing", "total"),
    ),
    Role(
        key="house_consumption",
        label="Hausverbrauch",
        hint="optional, wenn vorhanden genauer als der Zähler allein",
        units=frozenset(ENERGY_UNITS),
        positive=("hausverbrauch", "house_consumption", "home_consumption", "load", "verbrauch",
                  "consumption"),
        negative=("grid", "netz", "wallbox", "battery", "akku", "speicher", "heat", "waerme",
                  "cost", "price", "preis"),
        device_classes=("energy",),
        state_classes=("total_increasing", "total"),
    ),
    Role(
        key="pv_production",
        label="PV-Erzeugung",
        hint="optional, kWh-Zähler der Anlage",
        units=frozenset(ENERGY_UNITS),
        positive=("pv", "solar", "photovolt", "erzeugung", "production", "yield", "ertrag",
                  "inverter", "wechselrichter"),
        negative=("forecast", "prognose", "estimate", "cost", "price", "preis", "grid", "netz"),
        device_classes=("energy",),
        state_classes=("total_increasing", "total"),
    ),
    Role(
        key="pv_forecast_tomorrow",
        label="PV-Prognose Folgetag",
        hint="Forecast.Solar: Geschaetzte Energieproduktion morgen",
        units=frozenset(ENERGY_UNITS),
        positive=("energy_production_tomorrow", "forecast", "prognose", "estimate", "solcast"),
        negative=("today", "heute", "this_hour", "next_hour", "remaining"),
        required=True,
    ),
    Role(
        key="pv_forecast_today",
        label="PV-Prognose heute",
        hint="optional, für den Abgleich am selben Tag",
        units=frozenset(ENERGY_UNITS),
        positive=("energy_production_today", "forecast", "prognose", "estimate", "solcast"),
        negative=("tomorrow", "morgen", "this_hour", "next_hour", "remaining"),
    ),
    Role(
        key="battery_soc",
        label="Speicher-Ladezustand",
        hint="Prozent",
        units=frozenset({"%"}),
        positive=("battery", "batterie", "akku", "speicher", "soc", "ladezustand", "storage"),
        negative=("car", "auto", "vehicle", "ev_", "phone", "handy", "sensor_battery"),
        device_classes=("battery",),
        required=True,
    ),
    Role(
        key="battery_charge_energy",
        label="Speicher-Ladung (Energie)",
        hint="optional, kWh in den Speicher",
        units=frozenset(ENERGY_UNITS),
        positive=("battery_charge", "batterie_laden", "akku_laden", "speicher_laden", "charged",
                  "battery_in", "ladung"),
        negative=("discharge", "entlad", "out", "car", "auto", "wallbox"),
        device_classes=("energy",),
    ),
    Role(
        key="battery_discharge_energy",
        label="Speicher-Entladung (Energie)",
        hint="optional, kWh aus dem Speicher",
        units=frozenset(ENERGY_UNITS),
        positive=("battery_discharge", "entlad", "discharge", "battery_out", "speicher_entladen"),
        negative=("charge", "laden", "in_", "car", "auto", "wallbox"),
        device_classes=("energy",),
    ),
    Role(
        key="wallbox_energy",
        label="Wallbox (Ladeenergie)",
        hint="optional, kWh der Wallbox",
        units=frozenset(ENERGY_UNITS),
        positive=("wallbox", "charger", "evse", "ladepunkt", "go_e", "goe", "keba", "easee",
                  "openwb", "charging", "ladesaeule", "ev_"),
        negative=("battery", "akku", "speicher", "cost", "price", "preis"),
        device_classes=("energy",),
    ),
    Role(
        key="heatpump_energy",
        label="Wärmepumpe (Energie)",
        hint="optional, nur für das WP-Szenario",
        units=frozenset(ENERGY_UNITS),
        positive=("wärmepumpe", "warmepumpe", "heatpump", "heat_pump", "wp_", "heizung"),
        negative=("cost", "price", "preis"),
        device_classes=("energy",),
    ),
)

ROLES_BY_KEY = {role.key: role for role in ROLES}


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    name: str
    unit: str
    state: str
    device_class: str
    state_class: str
    score: int

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "unit": self.unit,
            "state": self.state,
            "device_class": self.device_class,
            "state_class": self.state_class,
            "score": self.score,
        }


def _haystack(entity: EntityState) -> str:
    return f"{entity.entity_id} {entity.friendly_name}".lower().replace("-", "_").replace(" ", "_")


def score_entity(entity: EntityState, role: Role) -> int:
    """Punktzahl für die Eignung einer Entität in einer Rolle; <= 0 heisst ungeeignet."""
    if not entity.entity_id.startswith("sensor."):
        return 0
    if entity.state in {"unavailable", "unknown", ""}:
        return 0

    unit = entity.unit.lower()
    if role.units and unit not in role.units:
        return 0

    text = _haystack(entity)
    if any(word in text for word in role.negative):
        return 0
    hits = sum(1 for word in role.positive if word in text)
    if hits == 0:
        return 0

    score = 10 * hits
    if role.device_classes and entity.device_class in role.device_classes:
        score += 15
    if role.state_classes and entity.state_class in role.state_classes:
        score += 15
    if unit == "kwh":
        score += 5
    if entity.as_float() is not None:
        score += 2
    return score


def discover(entities: Iterable[EntityState], roles: Sequence[Role] = ROLES) -> dict[str, list[Candidate]]:
    """Kandidaten je Rolle, absteigend nach Punktzahl."""
    materialised = list(entities)
    result: dict[str, list[Candidate]] = {}
    for role in roles:
        found: list[Candidate] = []
        for entity in materialised:
            score = score_entity(entity, role)
            if score <= 0:
                continue
            found.append(
                Candidate(
                    entity_id=entity.entity_id,
                    name=entity.friendly_name,
                    unit=entity.unit,
                    state=entity.state,
                    device_class=entity.device_class,
                    state_class=entity.state_class,
                    score=score,
                )
            )
        found.sort(key=lambda c: (-c.score, c.entity_id))
        result[role.key] = found
    return result


def suggest(entities: Iterable[EntityState]) -> dict[str, str]:
    """Bester Treffer je Rolle, ohne Mehrfachbelegung derselben Entität."""
    candidates = discover(entities)
    taken: set[str] = set()
    chosen: dict[str, str] = {}
    # Pflichtrollen zuerst, danach nach Punktzahl des besten Treffers.
    order = sorted(
        ROLES,
        key=lambda r: (not r.required, -(candidates[r.key][0].score if candidates[r.key] else 0)),
    )
    for role in order:
        for candidate in candidates[role.key]:
            if candidate.entity_id in taken:
                continue
            chosen[role.key] = candidate.entity_id
            taken.add(candidate.entity_id)
            break
        else:
            chosen[role.key] = ""
    return chosen
