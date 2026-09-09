"""Bewertung eines Tages: guenstigstes Ladefenster, Speicherpruefung, Ersparnis."""

from __future__ import annotations

import bisect
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from .battery import BaselineResult, StorageCheck, classify, pv_reserve_after, simulate_baseline
from .config import Battery, Settings, Tariff
from .forecast import DayForecast
from .timeutil import UTC, iso_utc

# Laengere Ladefenster als das bringen praktisch nichts und kosten nur Rechenzeit.
MAX_WINDOW_HOURS = 10.0


@dataclass
class ShiftPlan:
    """Ein bewertetes Ladefenster mit der daraus folgenden Verschiebung."""

    start_index: int
    end_index: int                       # exklusiv
    charge_kwh: list[float]              # zusaetzlicher Netzbezug je Slot
    displaced_kwh: list[float]           # dadurch vermiedener Netzbezug je Slot
    charge_cost_eur: float
    displaced_cost_eur: float
    stored_kwh: float
    delivered_kwh: float
    capacity_limited_kwh: float
    charge_power_limited_kwh: float
    discharge_power_limited_kwh: float
    capacity_available_kwh: float
    soc_at_start_kwh: float
    pv_reserved_kwh: float
    charge_capacity_kwh: float = 0.0   # eingangsseitig mögliche Ladung im Fenster

    @property
    def savings_eur(self) -> float:
        return self.displaced_cost_eur - self.charge_cost_eur

    @property
    def total_charge_kwh(self) -> float:
        return sum(self.charge_kwh)

    @property
    def total_displaced_kwh(self) -> float:
        return sum(self.displaced_kwh)

    def window_avg_price_ct(self, prices_ct: Sequence[float]) -> float:
        total = self.total_charge_kwh
        if total <= 1e-9:
            slots = prices_ct[self.start_index:self.end_index]
            return sum(slots) / len(slots) if slots else 0.0
        return sum(c * p for c, p in zip(self.charge_kwh, prices_ct)) / total


def _empty_plan(length: int) -> ShiftPlan:
    return ShiftPlan(
        start_index=0,
        end_index=0,
        charge_kwh=[0.0] * length,
        displaced_kwh=[0.0] * length,
        charge_cost_eur=0.0,
        displaced_cost_eur=0.0,
        stored_kwh=0.0,
        delivered_kwh=0.0,
        capacity_limited_kwh=0.0,
        charge_power_limited_kwh=0.0,
        discharge_power_limited_kwh=0.0,
        capacity_available_kwh=0.0,
        soc_at_start_kwh=0.0,
        pv_reserved_kwh=0.0,
    )


def _displacement_candidates(
    baseline: BaselineResult,
    prices_ct: Sequence[float],
    battery: Battery,
    duration_h: float,
    from_index: int,
) -> list[tuple[float, int, float]]:
    """Verdraengbarer Netzbezug ab from_index, teuerste Slots zuerst.

    Je Slot begrenzt durch die noch freie Entladeleistung des Speichers.
    """
    discharge_limit = battery.discharge_kw * duration_h
    rows: list[tuple[float, int, float]] = []
    for index in range(from_index, len(prices_ct)):
        headroom = max(0.0, discharge_limit - baseline.battery_discharge_kwh[index])
        amount = min(baseline.grid_import_kwh[index], headroom)
        if amount > 1e-9:
            rows.append((prices_ct[index], index, amount))
    rows.sort(key=lambda row: -row[0])
    return rows


def _evaluate_window(
    start: int,
    end: int,
    charge_slots: Sequence[tuple[float, int, float]],
    candidates: Sequence[tuple[float, int, float]],
    capacity_available_kwh: float,
    battery: Battery,
    slot_count: int,
) -> ShiftPlan:
    """Günstigste Ladung im Fenster gegen teuersten Bezug danach aufrechnen."""
    # Verschoben wird Energie, die aus dem Netz kommt: Ladegerät und Zellen rein,
    # Zellen und Wechselrichter raus.
    eff_c = battery.grid_charge_efficiency
    roundtrip = eff_c * battery.discharge_efficiency

    charge = [0.0] * slot_count
    displaced = [0.0] * slot_count
    charge_cost = 0.0
    displaced_cost = 0.0
    delivered = 0.0

    # Eingangsseitige Obergrenze aus der freien Kapazität.
    input_budget = capacity_available_kwh / eff_c if eff_c > 0 else 0.0
    charge_capacity_total = sum(row[2] for row in charge_slots)

    i = j = 0
    charge_left = charge_slots[i][2] if charge_slots else 0.0
    demand_left = candidates[j][2] if candidates else 0.0

    while i < len(charge_slots) and j < len(candidates) and input_budget > 1e-9:
        charge_price, charge_index, _ = charge_slots[i]
        demand_price, demand_index, _ = candidates[j]
        # Ein verschobenes kWh kostet den Ladepreis geteilt durch den Wirkungsgrad.
        if demand_price <= charge_price / roundtrip:
            break
        possible_out = min(charge_left, input_budget) * roundtrip
        take_out = min(possible_out, demand_left)
        if take_out <= 1e-9:
            break
        take_in = take_out / roundtrip

        charge[charge_index] += take_in
        displaced[demand_index] += take_out
        charge_cost += take_in * charge_price / 100.0
        displaced_cost += take_out * demand_price / 100.0
        delivered += take_out

        charge_left -= take_in
        input_budget -= take_in
        demand_left -= take_out
        if charge_left <= 1e-9:
            i += 1
            charge_left = charge_slots[i][2] if i < len(charge_slots) else 0.0
        if demand_left <= 1e-9:
            j += 1
            demand_left = candidates[j][2] if j < len(candidates) else 0.0

    stored = sum(charge) * eff_c
    # Die Zuordnung der Grenzen passiert in build_storage_check, wo der
    # preislich sinnvolle Bedarf bekannt ist.
    return ShiftPlan(
        start_index=start,
        end_index=end,
        charge_kwh=charge,
        displaced_kwh=displaced,
        charge_cost_eur=charge_cost,
        displaced_cost_eur=displaced_cost,
        stored_kwh=stored,
        delivered_kwh=delivered,
        capacity_limited_kwh=0.0,
        charge_power_limited_kwh=0.0,
        discharge_power_limited_kwh=0.0,
        capacity_available_kwh=capacity_available_kwh,
        soc_at_start_kwh=0.0,
        pv_reserved_kwh=0.0,
        charge_capacity_kwh=charge_capacity_total,
    )


def optimise(
    prices_ct: Sequence[float],
    baseline: BaselineResult,
    battery: Battery,
    duration_h: float,
    start_energy_kwh: float,
    unlimited: bool = False,
    max_window_hours: float = MAX_WINDOW_HOURS,
) -> ShiftPlan:
    """Bestes zusammenhaengendes Ladefenster nach erzielter Ersparnis.

    unlimited=True rechnet ohne Kapazitaets- und Leistungsgrenzen des Speichers
    und liefert damit den Vergleichswert "ohne Speichergrenzen".
    """
    slot_count = len(prices_ct)
    if slot_count == 0:
        return _empty_plan(0)

    # Netzladung läuft über das Ladegerät des Wechselrichters, PV dagegen über die
    # MPPT-Regler direkt auf die Gleichstromseite. Beide Wege teilen sich die
    # Ladeannahme des Speichers, haben aber unterschiedliche Obergrenzen.
    dc_limit = battery.charge_kw * duration_h
    grid_limit = battery.grid_charge_kw * duration_h
    max_slots = slot_count if unlimited else max(1, int(round(max_window_hours / duration_h)))
    candidate_cache: dict[int, list[tuple[float, int, float]]] = {}
    best = _empty_plan(slot_count)
    best_savings = 0.0

    for start in range(slot_count):
        charge_slots: list[tuple[float, int, float]] = []
        for end in range(start + 1, min(slot_count, start + max_slots) + 1):
            index = end - 1
            if unlimited:
                headroom = float("inf")
            else:
                frei = max(0.0, dc_limit - baseline.pv_to_battery_kwh[index])
                headroom = min(grid_limit, frei)
            if headroom > 1e-9:
                bisect.insort(charge_slots, (prices_ct[index], index, headroom))
            if not charge_slots:
                continue

            if end not in candidate_cache:
                candidate_cache[end] = _displacement_candidates(
                    baseline, prices_ct, battery, duration_h, end
                )
            candidates = candidate_cache[end]
            if not candidates:
                continue

            soc_at_start = baseline.soc_kwh[start - 1] if start > 0 else start_energy_kwh
            if unlimited:
                capacity_available = float("inf")
                pv_reserved = 0.0
            else:
                pv_reserved = pv_reserve_after(baseline.pv_surplus_kwh, start, battery, duration_h)
                capacity_available = max(0.0, battery.usable_kwh - soc_at_start - pv_reserved)
            if capacity_available <= 1e-9:
                continue

            plan = _evaluate_window(
                start, end, charge_slots, candidates, capacity_available, battery, slot_count
            )
            if plan.savings_eur > best_savings + 1e-9:
                plan.soc_at_start_kwh = soc_at_start
                plan.pv_reserved_kwh = 0.0 if unlimited else pv_reserved
                plan.capacity_available_kwh = 0.0 if unlimited else capacity_available
                best = plan
                best_savings = plan.savings_eur

    return _trim_window_start(best)


def _trim_window_start(plan: ShiftPlan) -> ShiftPlan:
    """Fensterbeginn auf den ersten tatsächlich geladenen Slot ziehen.

    Ein längeres Fenster wird nur gewählt, wenn es mehr bringt; die teuren
    Slots am Anfang bleiben dabei aber ungenutzt und würden das gemeldete
    Fenster unnötig aufblähen. Das Fensterende ist bereits knapp, weil ein
    kürzeres Fenster mit gleicher Ersparnis den Vorrang behält.
    """
    charged = [index for index, value in enumerate(plan.charge_kwh) if value > 1e-9]
    if charged:
        plan.start_index = charged[0]
    return plan


def build_storage_check(
    plan: ShiftPlan,
    baseline: BaselineResult,
    prices_ct: Sequence[float],
    battery: Battery,
    duration_h: float,
    window_price_ct: float,
) -> StorageCheck:
    """Bedarf und Speicherfähigkeit für das gewählte Fenster gegenüberstellen."""
    eff_d = battery.discharge_efficiency
    roundtrip = battery.grid_charge_efficiency * eff_d
    discharge_limit = battery.discharge_kw * duration_h
    window_hours = max(0.0, (plan.end_index - plan.start_index) * duration_h)
    has_window = plan.end_index > plan.start_index

    if not has_window:
        # Kein Fenster gefunden: bei diesem Preisverlauf lohnt keine Verschiebung.
        return StorageCheck(
            needed_kwh=0.0,
            shifted_kwh=0.0,
            capacity_needed_kwh=0.0,
            capacity_available_kwh=0.0,
            soc_at_window_start_kwh=0.0,
            pv_reserved_kwh=0.0,
            charge_power_needed_kw=0.0,
            charge_power_available_kw=battery.grid_charge_kw,
            capacity_limit_kwh=0.0,
            charge_power_limit_kwh=0.0,
            discharge_blocked_kwh=0.0,
            verdict="nicht_noetig",
            binding=[],
            reasons=["Preisunterschied deckt die Speicherverluste nicht"],
        )

    # Bedarf: Netzbezug nach dem Fenster, der teurer ist als Laden plus Verluste.
    threshold = window_price_ct / roundtrip if roundtrip > 0 else float("inf")
    needed = 0.0
    discharge_blocked = 0.0
    for index in range(plan.end_index, len(prices_ct)):
        if prices_ct[index] <= threshold:
            continue
        amount = baseline.grid_import_kwh[index]
        if amount <= 1e-9:
            continue
        needed += amount
        headroom = max(0.0, discharge_limit - baseline.battery_discharge_kwh[index])
        discharge_blocked += max(0.0, amount - headroom)

    shifted = plan.delivered_kwh
    capacity_limit = plan.capacity_available_kwh * eff_d
    charge_power_limit = plan.charge_capacity_kwh * roundtrip

    binding: list[str] = []
    reasons: list[str] = []
    tolerance = max(0.01, needed * 0.01)
    if capacity_limit < needed - tolerance:
        binding.append("kapazitaet")
        reasons.append(
            f"Kapazität: {plan.capacity_available_kwh:.1f} kWh frei zu Fensterbeginn "
            f"(SoC {plan.soc_at_start_kwh:.1f} kWh belegt, {plan.pv_reserved_kwh:.1f} kWh für PV "
            f"freigehalten) -> höchstens {capacity_limit:.1f} kWh verschiebbar"
        )
    if charge_power_limit < needed - tolerance:
        binding.append("ladeleistung")
        reasons.append(
            f"Netzladeleistung: {battery.grid_charge_kw:.1f} kW über {window_hours:.2f} h "
            f"-> höchstens {charge_power_limit:.1f} kWh verschiebbar"
        )
    if discharge_blocked > tolerance:
        binding.append("entladeleistung")
        reasons.append(
            f"Entladeleistung: {discharge_blocked:.1f} kWh Bezug liegt oberhalb "
            f"{battery.discharge_kw:.1f} kW und bleibt am Netz"
        )
    if plan.pv_reserved_kwh > 0.01 and "kapazitaet" in binding:
        binding.append("pv_vorrang")

    return StorageCheck(
        needed_kwh=needed,
        shifted_kwh=shifted,
        capacity_needed_kwh=plan.stored_kwh,
        capacity_available_kwh=plan.capacity_available_kwh,
        soc_at_window_start_kwh=plan.soc_at_start_kwh,
        pv_reserved_kwh=plan.pv_reserved_kwh,
        charge_power_needed_kw=plan.total_charge_kwh / window_hours if window_hours > 0 else 0.0,
        charge_power_available_kw=battery.grid_charge_kw,
        capacity_limit_kwh=capacity_limit,
        charge_power_limit_kwh=charge_power_limit,
        discharge_blocked_kwh=discharge_blocked,
        verdict=classify(needed, shifted),
        binding=binding,
        reasons=reasons,
    )


@dataclass
class DayEvaluation:
    """Vollstaendiges Tagesergebnis, direkt speicher- und anzeigbar."""

    day: date
    generated_at: datetime
    slots_utc: list[datetime]
    duration_h: float
    spot_eur_mwh: list[float]
    all_in_ct: list[float]
    household_kwh: list[float]
    ev_kwh: list[float]
    heatpump_kwh: list[float]
    pv_kwh: list[float]
    grid_import_kwh: list[float]
    soc_kwh: list[float]
    charge_kwh: list[float]
    displaced_kwh: list[float]
    storage: StorageCheck
    window_start_utc: datetime | None
    window_end_utc: datetime | None
    window_avg_price_ct: float
    day_avg_price_ct: float
    cheapest_slot_price_ct: float
    total_import_kwh: float
    total_pv_kwh: float
    cost_fixed_eur: float
    cost_dynamic_unshifted_eur: float
    cost_dynamic_shifted_eur: float
    cost_dynamic_ideal_eur: float
    base_price_delta_eur: float
    saving_vs_fixed_eur: float
    saving_vs_fixed_unshifted_eur: float
    saving_vs_fixed_ideal_eur: float
    saving_from_shifting_eur: float
    battery_usable_kwh: float
    scenario: str
    load_source: str
    pv_source: str
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def window_label(self) -> str:
        if self.window_start_utc is None or self.window_end_utc is None:
            return "kein Fenster"
        return f"{self.window_start_utc.isoformat()} - {self.window_end_utc.isoformat()}"

    def as_dict(self) -> dict:
        def r(values: Sequence[float], digits: int = 4) -> list[float]:
            return [round(float(v), digits) for v in values]

        return {
            "day": self.day.isoformat(),
            "generated_at": iso_utc(self.generated_at),
            "scenario": self.scenario,
            "duration_h": self.duration_h,
            "slots_utc": [iso_utc(s) for s in self.slots_utc],
            "series": {
                "spot_eur_mwh": r(self.spot_eur_mwh, 3),
                "all_in_ct": r(self.all_in_ct, 3),
                "household_kwh": r(self.household_kwh),
                "ev_kwh": r(self.ev_kwh),
                "heatpump_kwh": r(self.heatpump_kwh),
                "pv_kwh": r(self.pv_kwh),
                "grid_import_kwh": r(self.grid_import_kwh),
                "soc_kwh": r(self.soc_kwh),
                "charge_kwh": r(self.charge_kwh),
                "displaced_kwh": r(self.displaced_kwh),
            },
            "window": {
                "start_utc": iso_utc(self.window_start_utc) if self.window_start_utc else None,
                "end_utc": iso_utc(self.window_end_utc) if self.window_end_utc else None,
                "avg_price_ct": round(self.window_avg_price_ct, 3),
            },
            "prices": {
                "day_avg_ct": round(self.day_avg_price_ct, 3),
                "cheapest_slot_ct": round(self.cheapest_slot_price_ct, 3),
            },
            "energy": {
                "import_kwh": round(self.total_import_kwh, 3),
                "pv_kwh": round(self.total_pv_kwh, 3),
                "battery_usable_kwh": round(self.battery_usable_kwh, 3),
            },
            "storage": self.storage.as_dict(),
            "costs": {
                "fixed_eur": round(self.cost_fixed_eur, 4),
                "dynamic_unshifted_eur": round(self.cost_dynamic_unshifted_eur, 4),
                "dynamic_shifted_eur": round(self.cost_dynamic_shifted_eur, 4),
                "dynamic_ideal_eur": round(self.cost_dynamic_ideal_eur, 4),
                "base_price_delta_eur": round(self.base_price_delta_eur, 4),
            },
            "savings": {
                "vs_fixed_eur": round(self.saving_vs_fixed_eur, 4),
                "vs_fixed_unshifted_eur": round(self.saving_vs_fixed_unshifted_eur, 4),
                "vs_fixed_ideal_eur": round(self.saving_vs_fixed_ideal_eur, 4),
                "from_shifting_eur": round(self.saving_from_shifting_eur, 4),
            },
            "sources": {"load": self.load_source, "pv": self.pv_source},
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


def evaluate_day(
    forecast: DayForecast,
    spot_eur_mwh: Sequence[float],
    settings: Settings,
    tz: ZoneInfo,
    battery: Battery,
    start_soc_pct: float | None,
    generated_at: datetime | None = None,
    scenario: str = "standard",
    start_energy_kwh: float | None = None,
) -> DayEvaluation:
    """Preise, Prognose und Speicher zu einer Tagesbewertung zusammenfuehren."""
    tariff: Tariff = settings.tariff
    all_in_ct = [tariff.all_in_ct(value) for value in spot_eur_mwh]
    load = forecast.load_kwh
    duration_h = forecast.duration_h

    if start_energy_kwh is not None:
        start_energy = max(0.0, min(battery.usable_kwh, start_energy_kwh))
    else:
        # Ohne bekannten Ladezustand ist die Mitte der neutrale Ansatz: ein voll
        # angenommener Speicher wuerde die Ersparnis klein rechnen, ein leerer groß.
        soc_pct = (
            (settings.battery.soc_min_pct + settings.battery.soc_max_pct) / 2.0
            if start_soc_pct is None
            else start_soc_pct
        )
        start_energy = battery.energy_above_min(soc_pct)

    baseline = simulate_baseline(load, forecast.pv_kwh, battery, start_energy, duration_h)
    plan = optimise(all_in_ct, baseline, battery, duration_h, start_energy)
    ideal = optimise(all_in_ct, baseline, battery, duration_h, start_energy, unlimited=True)

    total_import = baseline.total_import_kwh
    cost_unshifted = sum(kwh * ct for kwh, ct in zip(baseline.grid_import_kwh, all_in_ct)) / 100.0
    cost_fixed = total_import * tariff.fixed_price_ct / 100.0
    cost_shifted = cost_unshifted - plan.savings_eur
    cost_ideal = cost_unshifted - ideal.savings_eur

    days_in_month = calendar.monthrange(forecast.day.year, forecast.day.month)[1]
    base_delta = (tariff.base_price_eur_month - tariff.fixed_base_price_eur_month) / days_in_month

    window_price = plan.window_avg_price_ct(all_in_ct) if plan.end_index > plan.start_index else 0.0
    storage = build_storage_check(plan, baseline, all_in_ct, battery, duration_h, window_price)

    day_avg = (cost_unshifted * 100.0 / total_import) if total_import > 1e-9 else (
        sum(all_in_ct) / len(all_in_ct) if all_in_ct else 0.0
    )

    warnings: list[str] = []
    saving_vs_fixed = cost_fixed - cost_shifted - base_delta
    if saving_vs_fixed < -0.005:
        warnings.append(
            f"Der dynamische Tarif wäre am {forecast.day.isoformat()} voraussichtlich "
            f"{abs(saving_vs_fixed):.2f} EUR teurer als der Fixtarif"
        )
    if storage.verdict == "teilweise":
        warnings.append(
            f"Speicher limitiert: nur {storage.shifted_kwh:.1f} von {storage.needed_kwh:.1f} kWh verschiebbar"
        )
    elif storage.verdict == "nicht_verschiebbar" and storage.needed_kwh > 0.1:
        warnings.append(
            f"Speicher kann die lohnenden {storage.needed_kwh:.1f} kWh nicht verschieben"
        )

    window_start = forecast.slots_utc[plan.start_index] if plan.end_index > plan.start_index else None
    window_end = (
        forecast.slots_utc[plan.end_index - 1] + timedelta(hours=duration_h)
        if plan.end_index > plan.start_index
        else None
    )

    return DayEvaluation(
        day=forecast.day,
        generated_at=generated_at or datetime.now(tz=UTC),
        slots_utc=list(forecast.slots_utc),
        duration_h=duration_h,
        spot_eur_mwh=list(spot_eur_mwh),
        all_in_ct=all_in_ct,
        household_kwh=list(forecast.household_kwh),
        ev_kwh=list(forecast.ev_kwh),
        heatpump_kwh=list(forecast.heatpump_kwh),
        pv_kwh=list(forecast.pv_kwh),
        grid_import_kwh=list(baseline.grid_import_kwh),
        soc_kwh=list(baseline.soc_kwh),
        charge_kwh=list(plan.charge_kwh),
        displaced_kwh=list(plan.displaced_kwh),
        storage=storage,
        window_start_utc=window_start,
        window_end_utc=window_end,
        window_avg_price_ct=window_price,
        day_avg_price_ct=day_avg,
        cheapest_slot_price_ct=min(all_in_ct) if all_in_ct else 0.0,
        total_import_kwh=total_import,
        total_pv_kwh=forecast.total_pv_kwh,
        cost_fixed_eur=cost_fixed,
        cost_dynamic_unshifted_eur=cost_unshifted,
        cost_dynamic_shifted_eur=cost_shifted,
        cost_dynamic_ideal_eur=cost_ideal,
        base_price_delta_eur=base_delta,
        saving_vs_fixed_eur=saving_vs_fixed,
        saving_vs_fixed_unshifted_eur=cost_fixed - cost_unshifted - base_delta,
        saving_vs_fixed_ideal_eur=cost_fixed - cost_ideal - base_delta,
        saving_from_shifting_eur=plan.savings_eur,
        battery_usable_kwh=battery.usable_kwh,
        scenario=scenario,
        load_source=forecast.load_source,
        pv_source=forecast.pv_source,
        warnings=warnings,
        notes=list(forecast.notes),
    )
