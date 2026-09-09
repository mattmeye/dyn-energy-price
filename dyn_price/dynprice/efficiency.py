"""Schätzung des Speicher-Wirkungsgrads aus den eigenen Zählern.

Victron-Systeme führen über den Shunt zwei Zähler mit: geladene und entladene
Energie. Deren Verhältnis ist der Wirkungsgrad des Speichers **auf der
Gleichstromseite**. Für die Bewertung zählt aber der Weg Netz → Speicher → Haus,
also zusätzlich Ladegerät und Wechselrichter. Beide Anteile werden hier getrennt
ausgewiesen, damit erkennbar bleibt, was gemessen und was angenommen ist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import Settings
from .hass import HomeAssistant, counter_to_hourly
from .timeutil import UTC

_LOG = logging.getLogger(__name__)

# Datenblattnahe Werte für Victron MultiPlus-II / Quattro.
DEFAULT_CHARGER_EFFICIENCY = 0.93
DEFAULT_INVERTER_EFFICIENCY = 0.94
# LFP-Speicher, falls sich nichts messen lässt.
FALLBACK_DC_ROUNDTRIP = 0.97

# Außerhalb dieser Grenzen ist das Verhältnis kein Wirkungsgrad, sondern ein
# Hinweis auf unpassend zugeordnete Zähler.
PLAUSIBLE_DC_RANGE = (0.80, 1.0)
MIN_CHARGED_KWH = 5.0


@dataclass
class EfficiencyEstimate:
    days: int
    charged_kwh: float
    discharged_kwh: float
    dc_roundtrip: float
    charger_efficiency: float
    inverter_efficiency: float
    ac_roundtrip: float
    measured: bool
    measurement_side: str            # "dc" oder "ac"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "charged_kwh": round(self.charged_kwh, 2),
            "discharged_kwh": round(self.discharged_kwh, 2),
            "dc_roundtrip": round(self.dc_roundtrip, 4),
            "charger_efficiency": round(self.charger_efficiency, 4),
            "inverter_efficiency": round(self.inverter_efficiency, 4),
            "ac_roundtrip": round(self.ac_roundtrip, 4),
            "measured": self.measured,
            "measurement_side": self.measurement_side,
            "notes": list(self.notes),
        }


def combine(
    dc_roundtrip: float,
    charger_efficiency: float = DEFAULT_CHARGER_EFFICIENCY,
    inverter_efficiency: float = DEFAULT_INVERTER_EFFICIENCY,
) -> float:
    """Wirkungsgrad des Weges Netz → Speicher → Haus."""
    return dc_roundtrip * charger_efficiency * inverter_efficiency


def from_counters(
    charged_kwh: float,
    discharged_kwh: float,
    days: int,
    measurement_side: str = "dc",
    charger_efficiency: float = DEFAULT_CHARGER_EFFICIENCY,
    inverter_efficiency: float = DEFAULT_INVERTER_EFFICIENCY,
) -> EfficiencyEstimate:
    """Aus den Zählerständen einen Wirkungsgrad ableiten, mit Plausibilitätsprüfung."""
    notes: list[str] = []
    measured = False
    ratio = discharged_kwh / charged_kwh if charged_kwh > 0 else 0.0

    if charged_kwh < MIN_CHARGED_KWH:
        notes.append(
            f"Zu wenig Umsatz für eine Messung ({charged_kwh:.1f} kWh geladen), "
            f"typischer LFP-Wert angesetzt"
        )
        dc = FALLBACK_DC_ROUNDTRIP
    elif not (PLAUSIBLE_DC_RANGE[0] <= ratio <= PLAUSIBLE_DC_RANGE[1]):
        notes.append(
            f"Verhältnis {ratio:.2f} liegt außerhalb des Plausiblen – die beiden Zähler "
            f"messen vermutlich nicht dieselbe Seite des Speichers; typischer LFP-Wert angesetzt"
        )
        dc = FALLBACK_DC_ROUNDTRIP
    else:
        dc = ratio
        measured = True

    if measurement_side == "ac":
        # Die Zähler messen bereits hinter Ladegerät und Wechselrichter.
        ac = dc
        notes.append("Zähler als wechselstromseitig gewertet, keine Wandlerverluste ergänzt")
    else:
        ac = combine(dc, charger_efficiency, inverter_efficiency)

    if measured and dc < 0.90:
        notes.append(
            "Der gemessene Speicherwirkungsgrad ist für LFP niedrig – "
            "hohe Ströme, kalte Zellen oder ein Zähler, der auch Wandlerverluste erfasst"
        )

    return EfficiencyEstimate(
        days=days,
        charged_kwh=charged_kwh,
        discharged_kwh=discharged_kwh,
        dc_roundtrip=dc,
        charger_efficiency=charger_efficiency if measurement_side != "ac" else 1.0,
        inverter_efficiency=inverter_efficiency if measurement_side != "ac" else 1.0,
        ac_roundtrip=max(0.5, min(0.99, ac)),
        measured=measured,
        measurement_side=measurement_side,
        notes=notes,
    )


def estimate(
    hass: HomeAssistant,
    settings: Settings,
    days: int = 30,
    measurement_side: str = "dc",
    charger_efficiency: float = DEFAULT_CHARGER_EFFICIENCY,
    inverter_efficiency: float = DEFAULT_INVERTER_EFFICIENCY,
) -> EfficiencyEstimate:
    """Zähler aus der Langzeitstatistik lesen und daraus den Wirkungsgrad schätzen."""
    charge_entity = settings.entities.battery_charge_energy
    discharge_entity = settings.entities.battery_discharge_energy
    if not charge_entity or not discharge_entity:
        result = from_counters(0.0, 0.0, days, measurement_side,
                               charger_efficiency, inverter_efficiency)
        result.notes.insert(
            0, "Ohne die Entitäten für Speicher-Ladung und -Entladung ist keine Messung möglich"
        )
        return result

    end = datetime.now(tz=UTC)
    start = end - timedelta(days=max(1, days))
    stats = hass.statistics([charge_entity, discharge_entity], start, end)
    charged = sum(value for _, value in counter_to_hourly(stats.get(charge_entity, [])))
    discharged = sum(value for _, value in counter_to_hourly(stats.get(discharge_entity, [])))

    result = from_counters(charged, discharged, days, measurement_side,
                           charger_efficiency, inverter_efficiency)
    if charged <= 0:
        result.notes.insert(0, "Die Langzeitstatistik lieferte keine Werte für den Speicher")
    return result
