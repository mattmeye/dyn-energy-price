"""Preisabruf, Einheiten, Slotzuordnung und Offline-Cache."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dynprice.prices import (
    PricePoint,
    PriceProvider,
    PriceSeries,
    PricesUnavailable,
    _parse_energy_charts,
    load_csv,
)
from dynprice.timeutil import day_bounds_utc, slot_starts_utc, tzinfo

TZ = tzinfo("Europe/Berlin")
DAY = date(2026, 9, 9)


def payload_for(day, minutes=15, count=200, unit="EUR/MWh", value=lambda i: 50.0 + i):
    start = day_bounds_utc(day, TZ)[0] - timedelta(hours=4)
    seconds, prices = [], []
    for index in range(count):
        seconds.append(int((start + timedelta(minutes=minutes * index)).timestamp()))
        prices.append(value(index))
    return {"unix_seconds": seconds, "price": prices, "unit": unit}


def test_energy_charts_antwort_wird_gelesen():
    series = _parse_energy_charts(payload_for(DAY))
    assert len(series) == 200
    assert series.resolution_minutes == 15


def test_einheiten_werden_auf_eur_pro_mwh_normiert():
    series = _parse_energy_charts(payload_for(DAY, unit="ct/kWh", value=lambda i: 8.0))
    assert series.points[0].spot_eur_mwh == 80.0
    series = _parse_energy_charts(payload_for(DAY, unit="EUR/kWh", value=lambda i: 0.08))
    assert round(series.points[0].spot_eur_mwh, 6) == 80.0


def test_stuendliche_quelle_deckt_viertelstunden_ab():
    series = _parse_energy_charts(payload_for(DAY, minutes=60, count=60))
    values = series.for_slots(slot_starts_utc(DAY, TZ))
    assert len(values) == 96
    assert values[0] == values[1] == values[2] == values[3]  # Treppenfunktion


def test_luecke_wird_nicht_ueberbrueckt():
    start = day_bounds_utc(DAY, TZ)[0]
    series = PriceSeries([
        PricePoint(start, 50.0),
        PricePoint(start + timedelta(minutes=15), 60.0),
        PricePoint(start + timedelta(hours=6), 70.0),
    ])
    assert series.at(start + timedelta(minutes=20)) == 60.0
    assert series.at(start + timedelta(hours=3)) is None


def test_offline_ohne_cache_meldet_fehlende_preise(tmp_path):
    provider = PriceProvider(tmp_path, offline=True)
    with pytest.raises(PricesUnavailable):
        provider.fetch_day(DAY, TZ)


def test_cache_erlaubt_wiederholte_laeufe_ohne_netz(tmp_path):
    provider = PriceProvider(tmp_path, offline=True)
    start, end = day_bounds_utc(DAY, TZ)
    provider._write_cache((start - timedelta(hours=2)).date(), (end + timedelta(hours=2)).date(),
                          payload_for(DAY))
    series = provider.fetch_day(DAY, TZ)
    assert len(series.for_slots(slot_starts_utc(DAY, TZ))) == 96


def test_csv_import(tmp_path):
    path = tmp_path / "preise.csv"
    path.write_text(
        "zeit,preis\n2026-09-09T00:00:00Z,42.5\n2026-09-09T01:00:00Z,-8.25\n", encoding="utf-8"
    )
    assert [p.spot_eur_mwh for p in load_csv(path)] == [42.5, -8.25]


def test_csv_import_mit_semikolon_und_dezimalkomma(tmp_path):
    path = tmp_path / "preise_de.csv"
    path.write_text(
        "zeit;preis\n2026-09-09T00:00:00Z;42,5\n2026-09-09T01:00:00Z;-8,25\n", encoding="utf-8"
    )
    assert [p.spot_eur_mwh for p in load_csv(path)] == [42.5, -8.25]


def test_leere_csv_wirft(tmp_path):
    path = tmp_path / "leer.csv"
    path.write_text("zeit,preis\n", encoding="utf-8")
    with pytest.raises(PricesUnavailable):
        load_csv(path)
