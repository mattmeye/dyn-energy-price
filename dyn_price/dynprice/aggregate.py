"""Monats- und Jahresauswertung aus Prognosen und Ist-Werten."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from .store import ActualRow, ForecastRow


@dataclass
class PeriodSummary:
    key: str                      # "2026-11" oder "2026"
    label: str
    days: int = 0
    days_with_actual: int = 0
    forecast_saving_eur: float = 0.0
    realised_saving_eur: float = 0.0
    forecast_import_kwh: float = 0.0
    actual_import_kwh: float = 0.0
    cost_fixed_eur: float = 0.0
    cost_dynamic_eur: float = 0.0
    days_capacity_limited: int = 0
    days_power_limited: int = 0
    days_fully_shiftable: int = 0
    days_dynamic_more_expensive: int = 0
    unused_capacity_kwh: float = 0.0
    needed_kwh: float = 0.0
    shifted_kwh: float = 0.0
    ideal_saving_eur: float = 0.0
    unshifted_saving_eur: float = 0.0

    @property
    def share_capacity_limited(self) -> float:
        return self.days_capacity_limited / self.days if self.days else 0.0

    @property
    def share_power_limited(self) -> float:
        return self.days_power_limited / self.days if self.days else 0.0

    @property
    def avg_unused_capacity_kwh(self) -> float:
        return self.unused_capacity_kwh / self.days if self.days else 0.0

    @property
    def saving_error_eur(self) -> float:
        return self.realised_saving_eur - self.forecast_saving_eur

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "days": self.days,
            "days_with_actual": self.days_with_actual,
            "forecast_saving_eur": round(self.forecast_saving_eur, 2),
            "realised_saving_eur": round(self.realised_saving_eur, 2),
            "saving_error_eur": round(self.saving_error_eur, 2),
            "ideal_saving_eur": round(self.ideal_saving_eur, 2),
            "unshifted_saving_eur": round(self.unshifted_saving_eur, 2),
            "forecast_import_kwh": round(self.forecast_import_kwh, 1),
            "actual_import_kwh": round(self.actual_import_kwh, 1),
            "cost_fixed_eur": round(self.cost_fixed_eur, 2),
            "cost_dynamic_eur": round(self.cost_dynamic_eur, 2),
            "days_capacity_limited": self.days_capacity_limited,
            "days_power_limited": self.days_power_limited,
            "days_fully_shiftable": self.days_fully_shiftable,
            "days_dynamic_more_expensive": self.days_dynamic_more_expensive,
            "share_capacity_limited": round(self.share_capacity_limited, 3),
            "share_power_limited": round(self.share_power_limited, 3),
            "avg_unused_capacity_kwh": round(self.avg_unused_capacity_kwh, 2),
            "needed_kwh": round(self.needed_kwh, 1),
            "shifted_kwh": round(self.shifted_kwh, 1),
        }


def _add_forecast(summary: PeriodSummary, row: ForecastRow) -> None:
    payload = row.payload
    storage = payload.get("storage", {})
    savings = payload.get("savings", {})
    binding = set(storage.get("binding", []))

    summary.days += 1
    summary.forecast_saving_eur += float(savings.get("vs_fixed_eur", 0.0))
    summary.ideal_saving_eur += float(savings.get("vs_fixed_ideal_eur", 0.0))
    summary.unshifted_saving_eur += float(savings.get("vs_fixed_unshifted_eur", 0.0))
    summary.forecast_import_kwh += float(payload.get("energy", {}).get("import_kwh", 0.0))
    summary.unused_capacity_kwh += float(storage.get("unused_capacity_kwh", 0.0))
    summary.needed_kwh += float(storage.get("needed_kwh", 0.0))
    summary.shifted_kwh += float(storage.get("shifted_kwh", 0.0))
    if "kapazitaet" in binding:
        summary.days_capacity_limited += 1
    if binding & {"ladeleistung", "entladeleistung"}:
        summary.days_power_limited += 1
    if storage.get("verdict") in {"verschiebbar", "nicht_noetig"}:
        summary.days_fully_shiftable += 1
    if float(savings.get("vs_fixed_eur", 0.0)) < 0:
        summary.days_dynamic_more_expensive += 1


def _add_actual(summary: PeriodSummary, row: ActualRow) -> None:
    payload = row.payload
    if payload.get("status") != "ok":
        return
    summary.days_with_actual += 1
    summary.realised_saving_eur += float(payload.get("saving_eur", 0.0))
    summary.actual_import_kwh += float(payload.get("import_kwh", 0.0))
    summary.cost_fixed_eur += float(payload.get("cost_fixed_eur", 0.0))
    summary.cost_dynamic_eur += float(payload.get("cost_dynamic_eur", 0.0))


def summarise(
    forecasts: Iterable[ForecastRow],
    actuals: Iterable[ActualRow],
    granularity: str = "month",
) -> list[PeriodSummary]:
    """Prognosen und Ist-Werte je Monat bzw. Jahr zusammenfassen."""

    def key_of(day: date) -> tuple[str, str]:
        if granularity == "year":
            return f"{day.year}", f"{day.year}"
        return f"{day.year}-{day.month:02d}", f"{day.month:02d}/{day.year}"

    summaries: dict[str, PeriodSummary] = {}
    for row in forecasts:
        key, label = key_of(row.day)
        _add_forecast(summaries.setdefault(key, PeriodSummary(key, label)), row)
    for actual in actuals:
        key, label = key_of(actual.day)
        _add_actual(summaries.setdefault(key, PeriodSummary(key, label)), actual)
    return [summaries[key] for key in sorted(summaries)]


def cumulative_savings(forecasts: Sequence[ForecastRow], actuals: Sequence[ActualRow]) -> list[dict[str, Any]]:
    """Kumulierte Ersparnis je Tag, Prognose und Ist nebeneinander."""
    actual_by_day = {row.day: row for row in actuals}
    running_forecast = 0.0
    running_actual = 0.0
    out: list[dict[str, Any]] = []
    for row in sorted(forecasts, key=lambda r: r.day):
        forecast_saving = float(row.payload.get("savings", {}).get("vs_fixed_eur", 0.0))
        running_forecast += forecast_saving
        actual = actual_by_day.get(row.day)
        actual_saving = None
        if actual is not None and actual.payload.get("status") == "ok":
            actual_saving = float(actual.payload.get("saving_eur", 0.0))
            running_actual += actual_saving
        out.append(
            {
                "day": row.day.isoformat(),
                "forecast_saving_eur": round(forecast_saving, 4),
                "actual_saving_eur": None if actual_saving is None else round(actual_saving, 4),
                "cumulative_forecast_eur": round(running_forecast, 4),
                "cumulative_actual_eur": round(running_actual, 4),
                "verdict": row.payload.get("storage", {}).get("verdict", ""),
            }
        )
    return out


def conclusion(summaries: Sequence[PeriodSummary]) -> list[str]:
    """Kurzfazit, höchstens zehn Zeilen."""
    if not summaries:
        return ["Noch keine Bewertung vorhanden."]
    total = PeriodSummary(key="gesamt", label="gesamt")
    for summary in summaries:
        total.days += summary.days
        total.days_with_actual += summary.days_with_actual
        total.forecast_saving_eur += summary.forecast_saving_eur
        total.realised_saving_eur += summary.realised_saving_eur
        total.ideal_saving_eur += summary.ideal_saving_eur
        total.unshifted_saving_eur += summary.unshifted_saving_eur
        total.days_capacity_limited += summary.days_capacity_limited
        total.days_power_limited += summary.days_power_limited
        total.days_dynamic_more_expensive += summary.days_dynamic_more_expensive
        total.unused_capacity_kwh += summary.unused_capacity_kwh
        total.forecast_import_kwh += summary.forecast_import_kwh
        total.actual_import_kwh += summary.actual_import_kwh

    lines = [
        f"Bewertete Tage: {total.days}, davon {total.days_with_actual} mit Ist-Abgleich.",
        f"Prognostizierte Ersparnis gegenüber Fixtarif: {total.forecast_saving_eur:.2f} EUR.",
    ]
    if total.days_with_actual:
        lines.append(f"Realisierte Ersparnis: {total.realised_saving_eur:.2f} EUR.")
        if total.forecast_saving_eur:
            deviation = total.realised_saving_eur - total.forecast_saving_eur
            lines.append(f"Abweichung Ist gegen Prognose: {deviation:+.2f} EUR.")
    lines.append(
        f"Ohne Verschiebung waeren es {total.unshifted_saving_eur:.2f} EUR, "
        f"ohne Speichergrenzen {total.ideal_saving_eur:.2f} EUR."
    )
    if total.days:
        lines.append(
            f"Kapazität limitierte an {total.days_capacity_limited} Tagen "
            f"({total.days_capacity_limited / total.days:.0%}), Leistung an "
            f"{total.days_power_limited} Tagen ({total.days_power_limited / total.days:.0%})."
        )
        lines.append(
            f"Im günstigen Fenster blieben im Mittel "
            f"{total.unused_capacity_kwh / total.days:.1f} kWh Speicher ungenutzt."
        )
        if total.days_dynamic_more_expensive:
            lines.append(
                f"An {total.days_dynamic_more_expensive} Tagen wäre der dynamische Tarif teurer gewesen."
            )
    if total.days:
        lines.append(f"Mittlere Ersparnis je Tag: {total.forecast_saving_eur / total.days:.2f} EUR.")
    return lines[:10]
