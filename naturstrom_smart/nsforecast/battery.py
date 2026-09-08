"""Speichersimulation und Prüfung, ob der Speicher die Verschiebung tragen kann."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .config import Battery


@dataclass
class BaselineResult:
    """Tagesverlauf ohne Netzladung: PV zuerst in den Verbrauch, dann in den Speicher."""

    grid_import_kwh: list[float]
    pv_to_load_kwh: list[float]
    pv_to_battery_kwh: list[float]
    pv_export_kwh: list[float]
    battery_discharge_kwh: list[float]
    soc_kwh: list[float]          # nutzbare Energie im Speicher am Slotende
    pv_surplus_kwh: list[float]

    @property
    def total_import_kwh(self) -> float:
        return sum(self.grid_import_kwh)

    @property
    def total_pv_to_battery_kwh(self) -> float:
        return sum(self.pv_to_battery_kwh)


def simulate_baseline(
    load_kwh: Sequence[float],
    pv_kwh: Sequence[float],
    battery: Battery,
    start_energy_kwh: float,
    duration_h: float,
) -> BaselineResult:
    """Verlauf, wie er sich ohne gezielte Netzladung ergibt.

    Reihenfolge je Slot: PV deckt den Verbrauch, Überschuss lädt den Speicher,
    der Rest wird eingespeist; verbleibender Verbrauch kommt aus dem Speicher
    und erst danach aus dem Netz.
    """
    usable = battery.usable_kwh
    charge_limit = battery.charge_kw * duration_h
    discharge_limit = battery.discharge_kw * duration_h
    eff_c = battery.charge_efficiency
    eff_d = battery.discharge_efficiency

    soc = max(0.0, min(usable, start_energy_kwh))
    grid: list[float] = []
    to_load: list[float] = []
    to_battery: list[float] = []
    export: list[float] = []
    discharge: list[float] = []
    soc_series: list[float] = []
    surplus_series: list[float] = []

    for load, pv in zip(load_kwh, pv_kwh):
        direct = min(load, pv)
        residual = load - direct
        surplus = pv - direct
        surplus_series.append(surplus)

        charge_in = min(surplus, charge_limit, (usable - soc) / eff_c if eff_c > 0 else 0.0)
        charge_in = max(0.0, charge_in)
        soc += charge_in * eff_c
        exported = surplus - charge_in

        available_out = soc * eff_d
        out = min(residual, discharge_limit, available_out)
        out = max(0.0, out)
        soc -= out / eff_d if eff_d > 0 else 0.0
        residual -= out

        grid.append(max(0.0, residual))
        to_load.append(direct)
        to_battery.append(charge_in)
        export.append(max(0.0, exported))
        discharge.append(out)
        soc_series.append(soc)

    return BaselineResult(
        grid_import_kwh=grid,
        pv_to_load_kwh=to_load,
        pv_to_battery_kwh=to_battery,
        pv_export_kwh=export,
        battery_discharge_kwh=discharge,
        soc_kwh=soc_series,
        pv_surplus_kwh=surplus_series,
    )


@dataclass
class StorageCheck:
    """Gegenüberstellung von Bedarf und Speicherfähigkeit für ein Ladefenster.

    Die Grenzen werden als jeweils allein erreichbare Verschiebemenge angegeben,
    damit sich derselbe Fehlbetrag nicht mehrfach in den Gruenden wiederfindet.
    """

    needed_kwh: float               # Energie, deren Verschiebung sich preislich lohnt
    shifted_kwh: float              # davon tatsächlich verschiebbar
    capacity_needed_kwh: float      # dafuer noetige Speicherladung
    capacity_available_kwh: float   # freie Kapazität zu Fensterbeginn nach PV-Vorrang
    soc_at_window_start_kwh: float
    pv_reserved_kwh: float          # für PV-Überschuss freigehaltene Kapazität
    charge_power_needed_kw: float
    charge_power_available_kw: float
    capacity_limit_kwh: float       # allein durch Kapazität mögliche Menge
    charge_power_limit_kwh: float   # allein durch Ladeleistung im Fenster mögliche Menge
    discharge_blocked_kwh: float    # Bezug oberhalb der Entladeleistung
    verdict: str                    # verschiebbar | teilweise | nicht_verschiebbar | nicht_noetig
    binding: list[str] = field(default_factory=list)   # tatsächlich begrenzende Groessen
    reasons: list[str] = field(default_factory=list)

    @property
    def not_shifted_kwh(self) -> float:
        return max(0.0, self.needed_kwh - self.shifted_kwh)

    @property
    def unused_capacity_kwh(self) -> float:
        return max(0.0, self.capacity_available_kwh - self.capacity_needed_kwh)

    def as_dict(self) -> dict:
        return {
            "needed_kwh": round(self.needed_kwh, 3),
            "shifted_kwh": round(self.shifted_kwh, 3),
            "not_shifted_kwh": round(self.not_shifted_kwh, 3),
            "capacity_needed_kwh": round(self.capacity_needed_kwh, 3),
            "capacity_available_kwh": round(self.capacity_available_kwh, 3),
            "unused_capacity_kwh": round(self.unused_capacity_kwh, 3),
            "soc_at_window_start_kwh": round(self.soc_at_window_start_kwh, 3),
            "pv_reserved_kwh": round(self.pv_reserved_kwh, 3),
            "charge_power_needed_kw": round(self.charge_power_needed_kw, 3),
            "charge_power_available_kw": round(self.charge_power_available_kw, 3),
            "capacity_limit_kwh": round(self.capacity_limit_kwh, 3),
            "charge_power_limit_kwh": round(self.charge_power_limit_kwh, 3),
            "discharge_blocked_kwh": round(self.discharge_blocked_kwh, 3),
            "verdict": self.verdict,
            "binding": list(self.binding),
            "reasons": list(self.reasons),
        }


def classify(needed_kwh: float, shifted_kwh: float, tolerance: float = 0.02) -> str:
    if needed_kwh <= 1e-6:
        return "nicht_noetig"
    if shifted_kwh <= 1e-6:
        return "nicht_verschiebbar"
    if shifted_kwh >= needed_kwh * (1.0 - tolerance):
        return "verschiebbar"
    return "teilweise"


VERDICT_LABELS = {
    "verschiebbar": "verschiebbar",
    "teilweise": "teilweise verschiebbar",
    "nicht_verschiebbar": "nicht verschiebbar",
    "nicht_noetig": "keine Verschiebung nötig",
}


def pv_reserve_after(
    pv_surplus_kwh: Sequence[float],
    from_index: int,
    battery: Battery,
    duration_h: float,
) -> float:
    """Kapazität, die nach Fensterende noch für PV-Überschuss gebraucht wird."""
    charge_limit = battery.charge_kw * duration_h
    stored = sum(
        min(max(0.0, surplus), charge_limit) * battery.charge_efficiency
        for surplus in pv_surplus_kwh[from_index:]
    )
    return min(stored, battery.usable_kwh)
