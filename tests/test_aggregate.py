"""Monats- und Jahreszusammenfassung."""

from __future__ import annotations

from datetime import date, timedelta

from dynprice.aggregate import conclusion, cumulative_savings, summarise
from dynprice.store import ActualRow, ForecastRow


def forecast_row(day, saving=1.0, binding=(), verdict="verschiebbar", unused=2.0, import_kwh=20.0):
    return ForecastRow(
        day=day,
        scenario="standard",
        generated_at="2026-11-19T12:00:00Z",
        payload={
            "savings": {
                "vs_fixed_eur": saving,
                "vs_fixed_ideal_eur": saving + 0.5,
                "vs_fixed_unshifted_eur": saving - 0.4,
            },
            "energy": {"import_kwh": import_kwh},
            "storage": {
                "verdict": verdict, "binding": list(binding),
                "unused_capacity_kwh": unused, "needed_kwh": 10.0, "shifted_kwh": 8.0,
            },
        },
    )


def actual_row(day, saving=0.8):
    return ActualRow(day, "2026-11-21T06:00:00Z", {
        "status": "ok", "saving_eur": saving, "import_kwh": 21.0,
        "cost_fixed_eur": 6.5, "cost_dynamic_eur": 5.2,
    })


def test_monatssummen():
    tage = [date(2026, 11, 1) + timedelta(days=i) for i in range(3)]
    summaries = summarise([forecast_row(d) for d in tage], [actual_row(tage[0])], "month")
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.days == 3
    assert summary.days_with_actual == 1
    assert round(summary.forecast_saving_eur, 2) == 3.0
    assert round(summary.realised_saving_eur, 2) == 0.8


def test_monate_werden_getrennt():
    rows = [forecast_row(date(2026, 11, 30)), forecast_row(date(2026, 12, 1))]
    summaries = summarise(rows, [], "month")
    assert [s.key for s in summaries] == ["2026-11", "2026-12"]
    jahre = summarise(rows, [], "year")
    assert [s.key for s in jahre] == ["2026"]
    assert jahre[0].days == 2


def test_limitquoten():
    rows = [
        forecast_row(date(2026, 11, 1), binding=["kapazitaet"], verdict="teilweise"),
        forecast_row(date(2026, 11, 2), binding=["entladeleistung"], verdict="teilweise"),
        forecast_row(date(2026, 11, 3)),
        forecast_row(date(2026, 11, 4)),
    ]
    summary = summarise(rows, [], "month")[0]
    assert summary.days_capacity_limited == 1
    assert summary.days_power_limited == 1
    assert summary.days_fully_shiftable == 2
    assert summary.share_capacity_limited == 0.25
    assert summary.avg_unused_capacity_kwh == 2.0


def test_teure_tage_werden_gezaehlt():
    rows = [forecast_row(date(2026, 11, 1), saving=-0.5), forecast_row(date(2026, 11, 2))]
    assert summarise(rows, [], "month")[0].days_dynamic_more_expensive == 1


def test_kumulierte_ersparnis():
    tage = [date(2026, 11, 1) + timedelta(days=i) for i in range(3)]
    rows = cumulative_savings([forecast_row(d) for d in tage], [actual_row(tage[0])])
    assert [round(r["cumulative_forecast_eur"], 2) for r in rows] == [1.0, 2.0, 3.0]
    assert rows[0]["actual_saving_eur"] == 0.8
    assert rows[1]["actual_saving_eur"] is None
    assert round(rows[2]["cumulative_actual_eur"], 2) == 0.8


def test_fazit_bleibt_kurz():
    tage = [date(2026, 11, 1) + timedelta(days=i) for i in range(20)]
    summaries = summarise([forecast_row(d, binding=["kapazitaet"]) for d in tage],
                          [actual_row(d) for d in tage], "month")
    lines = conclusion(summaries)
    assert 1 <= len(lines) <= 10
    assert any("Prognostizierte Ersparnis" in line for line in lines)


def test_fazit_ohne_daten():
    assert conclusion([]) == ["Noch keine Bewertung vorhanden."]
