"""Schätzung des Speicher-Wirkungsgrads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nsforecast.efficiency import (
    DEFAULT_CHARGER_EFFICIENCY,
    DEFAULT_INVERTER_EFFICIENCY,
    FALLBACK_DC_ROUNDTRIP,
    combine,
    estimate,
    from_counters,
)
from nsforecast.hass import StatPoint


class FakeHass:
    """Liefert Zählerstände als Langzeitstatistik."""

    configured = True

    def __init__(self, charged=None, discharged=None):
        self.charged = charged
        self.discharged = discharged

    def statistics(self, entity_ids, start, end, period="hour"):
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)

        def counter(total):
            if total is None:
                return []
            return [StatPoint(base + timedelta(hours=i), total * i / 10.0) for i in range(11)]

        out = {}
        for entity_id in entity_ids:
            if "charge" in entity_id and "dis" not in entity_id:
                out[entity_id] = counter(self.charged)
            else:
                out[entity_id] = counter(self.discharged)
        return out


def test_wandlerverluste_werden_ergaenzt():
    assert round(combine(0.97), 4) == round(0.97 * 0.93 * 0.94, 4)


def test_gemessenes_verhaeltnis_wird_uebernommen():
    result = from_counters(charged_kwh=900.0, discharged_kwh=873.0, days=30)
    assert result.measured is True
    assert round(result.dc_roundtrip, 3) == 0.970
    assert 0.83 < result.ac_roundtrip < 0.86


def test_zu_wenig_umsatz_faellt_auf_den_typischen_wert_zurueck():
    result = from_counters(charged_kwh=2.0, discharged_kwh=1.9, days=30)
    assert result.measured is False
    assert result.dc_roundtrip == FALLBACK_DC_ROUNDTRIP
    assert any("Umsatz" in note for note in result.notes)


def test_unplausibles_verhaeltnis_wird_verworfen():
    result = from_counters(charged_kwh=900.0, discharged_kwh=1100.0, days=30)
    assert result.measured is False
    assert result.dc_roundtrip == FALLBACK_DC_ROUNDTRIP
    assert any("Plausiblen" in note for note in result.notes)


def test_niedriger_messwert_wird_kommentiert():
    result = from_counters(charged_kwh=900.0, discharged_kwh=760.0, days=30)
    assert result.measured is True
    assert any("niedrig" in note for note in result.notes)


def test_wechselstromseitige_messung_ergaenzt_nichts():
    result = from_counters(900.0, 765.0, 30, measurement_side="ac")
    assert round(result.ac_roundtrip, 3) == 0.850
    assert result.charger_efficiency == 1.0


def test_eigene_wandlerwirkungsgrade():
    result = from_counters(900.0, 873.0, 30, charger_efficiency=0.90, inverter_efficiency=0.90)
    assert round(result.ac_roundtrip, 4) == round(0.97 * 0.90 * 0.90, 4)


def test_ergebnis_bleibt_im_sinnvollen_bereich():
    result = from_counters(900.0, 810.0, 30, charger_efficiency=0.5, inverter_efficiency=0.5)
    assert result.ac_roundtrip >= 0.5


def test_schaetzung_aus_home_assistant(settings):
    settings.entities.battery_charge_energy = "sensor.battery_charged_energy"
    settings.entities.battery_discharge_energy = "sensor.battery_discharged_energy"
    result = estimate(FakeHass(charged=800.0, discharged=776.0), settings, days=30)
    assert result.measured is True
    assert round(result.dc_roundtrip, 2) == 0.97
    assert result.charged_kwh == 800.0


def test_ohne_entitaeten_keine_messung(settings):
    result = estimate(FakeHass(), settings, days=30)
    assert result.measured is False
    assert any("Entitäten" in note for note in result.notes)
    assert round(result.ac_roundtrip, 2) == round(
        FALLBACK_DC_ROUNDTRIP * DEFAULT_CHARGER_EFFICIENCY * DEFAULT_INVERTER_EFFICIENCY, 2
    )


def test_leere_statistik_wird_gemeldet(settings):
    settings.entities.battery_charge_energy = "sensor.battery_charged_energy"
    settings.entities.battery_discharge_energy = "sensor.battery_discharged_energy"
    result = estimate(FakeHass(charged=None, discharged=None), settings, days=30)
    assert result.measured is False
    assert any("Langzeitstatistik" in note for note in result.notes)
