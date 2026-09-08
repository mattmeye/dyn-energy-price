"""Lade- und Entladegrenzen, wahlweise statisch oder live aus Home Assistant.

Victron gibt die Grenzen als **Strom** vor: DVCC meldet eine Ladestromgrenze (CCL)
und eine Entladestromgrenze (DCL) in Ampere, und das BMS senkt beide bei kalten
Zellen oder hohem Ladezustand ab. Aus Ampere wird hier mit der Batteriespannung
eine Leistung. Getrennt davon steht die Eingangsstrombegrenzung des
Wechselrichters, die begrenzt, was überhaupt aus dem Netz durch das Gerät fließt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import Battery, Entities
from .hass import EntityState, HomeAssistant
from .timeutil import UTC

_LOG = logging.getLogger(__name__)

# Ein Wert, der deutlich unter dem Tagesschnitt liegt, deutet auf eine
# Absenkung durch das BMS hin - bei kalten Zellen der Regelfall.
LOW_LIMIT_RATIO = 0.8


@dataclass
class ResolvedLimits:
    charge_kw: float          # was der Speicher gleichstromseitig annimmt
    grid_charge_kw: float     # was davon über das Ladegerät aus dem Netz kommt
    discharge_kw: float
    soc_min_pct: float | None
    battery_voltage_v: float
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "charge_kw": round(self.charge_kw, 3),
            "grid_charge_kw": round(self.grid_charge_kw, 3),
            "discharge_kw": round(self.discharge_kw, 3),
            "soc_min_pct": None if self.soc_min_pct is None else round(self.soc_min_pct, 1),
            "battery_voltage_v": round(self.battery_voltage_v, 1),
            "sources": dict(self.sources),
            "notes": list(self.notes),
        }


def power_kw(state: EntityState | None, voltage_v: float) -> float | None:
    """Zustand einer Grenz-Entität in Leistung umrechnen.

    Ampere wird mit der Batteriespannung multipliziert; genau so gibt Victron
    die DVCC-Grenzen aus.
    """
    if state is None:
        return None
    value = state.as_float()
    if value is None or value < 0:
        return None
    unit = state.unit.strip().lower()
    if unit in {"kw", "kva"}:
        return value
    if unit in {"w", "va"}:
        return value / 1000.0
    if unit == "a":
        return value * voltage_v / 1000.0
    if unit == "ma":
        return value * voltage_v / 1_000_000.0
    return None


def _voltage(hass: HomeAssistant, battery: Battery, entities: Entities, notes: list[str]) -> float:
    if entities.battery_voltage:
        state = hass.state(entities.battery_voltage)
        value = state.as_float() if state else None
        if value and value > 10.0:
            return value
        notes.append(
            f"Batteriespannung {entities.battery_voltage} nicht lesbar, "
            f"{battery.nominal_voltage_v:.1f} V angesetzt"
        )
    return battery.nominal_voltage_v


def _recent_minimum(hass: HomeAssistant, entity_id: str, days: int) -> float | None:
    """Kleinster Stundenwert der letzten Tage, falls die Statistik ihn führt."""
    if not entity_id or days <= 0:
        return None
    end = datetime.now(tz=UTC)
    stats = hass.statistics([entity_id], end - timedelta(days=days), end)
    values = [p.minimum for p in stats.get(entity_id, []) if p.minimum is not None]
    return min(values) if values else None


def _live(
    hass: HomeAssistant,
    entity_id: str,
    fallback_kw: float,
    voltage_v: float,
    label: str,
    sources: dict[str, str],
    notes: list[str],
    history_days: int,
) -> float:
    """Grenze aus einer Entität lesen, sonst den statischen Wert nehmen."""
    if not entity_id:
        sources[label] = "fest eingestellt"
        return fallback_kw

    state = hass.state(entity_id)
    value = power_kw(state, voltage_v)
    if value is None:
        einheit = f" (Einheit {state.unit!r})" if state is not None and state.unit else ""
        notes.append(f"{label}: {entity_id} nicht auswertbar{einheit}, fester Wert verwendet")
        sources[label] = "fest eingestellt"
        return fallback_kw

    sources[label] = f"{entity_id} ({state.state} {state.unit})".strip()

    # Der Lauf findet nachmittags statt, gerechnet wird die Nacht. Sinkt die
    # Grenze zeitweise deutlich ab, ist der Momentanwert zu optimistisch.
    tief = _recent_minimum(hass, entity_id, history_days)
    if tief is not None:
        tief_kw = tief * voltage_v / 1000.0 if (state and state.unit.strip().lower() == "a") else (
            tief / 1000.0 if (state and state.unit.strip().lower() in {"w", "va"}) else tief
        )
        if tief_kw < value * LOW_LIMIT_RATIO:
            notes.append(
                f"{label}: aktuell {value:.1f} kW, in den letzten {history_days} Tagen "
                f"zeitweise nur {tief_kw:.1f} kW - das BMS senkt die Grenze ab "
                f"(kalte Zellen oder hoher Ladezustand)"
            )
    return value


def resolve(
    hass: HomeAssistant,
    battery: Battery,
    entities: Entities | None = None,
    history_days: int = 7,
) -> ResolvedLimits:
    """Die für den Lauf gültigen Grenzen bestimmen."""
    entities = entities or Entities()
    notes: list[str] = []
    sources: dict[str, str] = {}
    voltage = _voltage(hass, battery, entities, notes)

    charge = _live(hass, entities.battery_charge_limit, battery.charge_kw, voltage,
                   "Ladeleistung", sources, notes, history_days)
    discharge = _live(hass, entities.battery_discharge_limit, battery.discharge_kw, voltage,
                      "Entladeleistung", sources, notes, history_days)
    # Das Ladegerät ist eine Geräteeigenschaft; die DVCC-Grenze deckelt es zusätzlich.
    grid_charge = min(battery.grid_charge_kw, charge)

    # Die Eingangsstrombegrenzung deckelt zusätzlich, was aus dem Netz durch den
    # Wechselrichter fließt - Verbraucher an dessen Ausgang teilen sich das mit.
    ac_limit_a = battery.ac_input_limit_a
    if entities.ac_input_limit:
        state = hass.state(entities.ac_input_limit)
        value = state.as_float() if state else None
        if value and value > 0:
            ac_limit_a = value
            sources["Eingangsstrom"] = f"{entities.ac_input_limit} ({value:g} A)"
        else:
            notes.append(f"Eingangsstromgrenze {entities.ac_input_limit} nicht lesbar")
    if ac_limit_a > 0:
        ac_kw = ac_limit_a * battery.mains_voltage_v * max(1, battery.phases) / 1000.0
        if ac_kw < grid_charge:
            notes.append(
                f"Netzladung durch die Eingangsstromgrenze auf {ac_kw:.1f} kW begrenzt "
                f"({ac_limit_a:g} A, {battery.phases} Phase(n))"
            )
            grid_charge = ac_kw

    if battery.grid_charge_kw > charge:
        notes.append(
            f"Netzladeleistung auf die Ladeannahme des Speichers begrenzt ({charge:.1f} kW)"
        )
    sources.setdefault("Netzladeleistung", "fest eingestellt")

    soc_min: float | None = None
    if entities.battery_soc_min:
        state = hass.state(entities.battery_soc_min)
        value = state.as_float() if state else None
        if value is not None and 0.0 <= value <= 100.0:
            soc_min = value
            sources["Mindest-SoC"] = f"{entities.battery_soc_min} ({value:g} %)"
        else:
            notes.append(f"Mindest-SoC {entities.battery_soc_min} nicht lesbar")

    return ResolvedLimits(
        charge_kw=charge,
        grid_charge_kw=grid_charge,
        discharge_kw=discharge,
        soc_min_pct=soc_min,
        battery_voltage_v=voltage,
        sources=sources,
        notes=notes,
    )


def apply(battery: Battery, limits: ResolvedLimits) -> Battery:
    """Eine Kopie des Speichers mit den aufgelösten Grenzen."""
    from dataclasses import replace

    updated = replace(
        battery,
        charge_kw=limits.charge_kw,
        grid_charge_kw=limits.grid_charge_kw,
        discharge_kw=limits.discharge_kw,
    )
    if limits.soc_min_pct is not None:
        updated.soc_min_pct = limits.soc_min_pct
    return updated
