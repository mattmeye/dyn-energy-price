"""Zeitraster und Zeitumstellung."""

from __future__ import annotations

from datetime import date

from nsforecast.timeutil import in_window, iso_utc, parse_iso, slot_starts_utc, to_local, tzinfo

TZ = tzinfo("Europe/Berlin")


def test_normaler_tag_hat_96_slots():
    assert len(slot_starts_utc(date(2026, 9, 9), TZ)) == 96


def test_zeitumstellung_kuerzt_und_verlaengert_den_tag():
    assert len(slot_starts_utc(date(2026, 3, 29), TZ)) == 92
    assert len(slot_starts_utc(date(2026, 10, 25), TZ)) == 100


def test_slots_beginnen_zur_lokalen_mitternacht():
    first = slot_starts_utc(date(2026, 9, 9), TZ)[0]
    assert to_local(first, TZ).hour == 0
    assert to_local(first, TZ).minute == 0


def test_fenster_ueber_mitternacht():
    assert in_window(23.0, 22, 6) is True
    assert in_window(3.0, 22, 6) is True
    assert in_window(7.0, 22, 6) is False
    assert in_window(10.0, 8, 18) is True


def test_iso_parser_akzeptiert_z_und_offset():
    assert parse_iso("2026-09-09T10:00:00Z") == parse_iso("2026-09-09T12:00:00+02:00")
    assert iso_utc(parse_iso("2026-09-09T12:00:00+02:00")) == "2026-09-09T10:00:00Z"


def test_unbekannte_zeitzone_faellt_auf_berlin_zurueck():
    assert str(tzinfo("Gibt/EsNicht")) == "Europe/Berlin"
