"""Ablauf eines Laufs: Daten holen, bewerten, ablegen, veroeffentlichen."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import AddonOptions, Settings, SettingsStore
from .battery import simulate_baseline
from .evaluate import DayEvaluation, evaluate_day
from .forecast import HistoryBundle, build_day_forecast
from .hass import HomeAssistant, StatPoint, counter_to_hourly
from .limits import apply as apply_limits
from .limits import resolve as resolve_limits
from .prices import PriceProvider, PricesUnavailable
from .mqttpublish import MqttPublisher, broker_from_supervisor
from .publish import publish as publish_via_rest
from .reconcile import backfill
from .store import Store
from .timeutil import SLOT_HOURS, UTC, slot_starts_utc, tzinfo

_LOG = logging.getLogger(__name__)


def _align(base: list[tuple[datetime, float]], subtract: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Zweite Reihe stundenweise von der ersten abziehen, nie unter null."""
    lookup = {moment: value for moment, value in subtract}
    return [(moment, max(0.0, value - lookup.get(moment, 0.0))) for moment, value in base]


def _hourly_from_history(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Ersatz für die Langzeitstatistik: Zählerstände aus dem Recorder stündlich bündeln."""
    if not entity_id:
        return []
    try:
        rows = hass.history(entity_id, start, end)
    except Exception as err:
        _LOG.info("Historie für %s nicht lesbar: %s", entity_id, err)
        return []
    if not rows:
        return []
    hourly: dict[datetime, float] = {}
    for moment, value in rows:
        hour = moment.replace(minute=0, second=0, microsecond=0)
        hourly[hour] = value  # letzter Zählerstand der Stunde
    points = [StatPoint(hour, value) for hour, value in sorted(hourly.items())]
    return counter_to_hourly(points)


class Runner:
    """Fuehrt die tägliche Bewertung aus."""

    def __init__(
        self,
        options: AddonOptions,
        settings_store: SettingsStore,
        store: Store,
        hass: HomeAssistant,
        prices: PriceProvider,
    ) -> None:
        self.options = options
        self.settings_store = settings_store
        self.store = store
        self.hass = hass
        self.prices = prices
        self.tz: ZoneInfo = tzinfo(options.timezone)

    # ------------------------------------------------------------ Datenbasis

    def collect_history(self, settings: Settings, target_day: date) -> HistoryBundle:
        entities = settings.entities
        end = datetime.now(tz=UTC)
        start = end - timedelta(days=max(1, settings.forecast.history_days))
        notes: list[str] = []

        wanted = [
            entities.grid_import,
            entities.house_consumption,
            entities.wallbox_energy,
            entities.heatpump_energy,
        ]
        stats = self.hass.statistics([e for e in wanted if e], start, end)

        def series(entity_id: str) -> list[tuple[datetime, float]]:
            if not entity_id:
                return []
            points = stats.get(entity_id, [])
            rows = counter_to_hourly(points)
            if rows:
                return rows
            fallback = _hourly_from_history(self.hass, entity_id, start, end)
            if fallback:
                notes.append(f"{entity_id}: Recorder-Historie statt Langzeitstatistik")
            return fallback

        if entities.house_consumption:
            base = series(entities.house_consumption)
            basis = "hausverbrauch"
        else:
            base = series(entities.grid_import)
            basis = "netzbezug"
            if base:
                notes.append(
                    "Basis Netzbezug: PV-Eigenverbrauch ist im Profil bereits enthalten"
                )
        if not base:
            notes.append("Keine Verbrauchshistorie verfügbar, Monatsreferenz wird verwendet")

        wallbox = series(entities.wallbox_energy)
        heatpump = series(entities.heatpump_energy)
        household = base
        if wallbox:
            household = _align(household, wallbox)
            notes.append("Wallbox-Energie aus dem Haushaltsprofil herausgerechnet")
        if heatpump:
            household = _align(household, heatpump)
            notes.append("Wärmepumpe aus dem Haushaltsprofil herausgerechnet")

        today = datetime.now(tz=self.tz).date()
        forecast_entity = (
            entities.pv_forecast_tomorrow if target_day > today else entities.pv_forecast_today
        )
        pv_state = self.hass.state(forecast_entity) if forecast_entity else None
        if forecast_entity and pv_state is None:
            notes.append(f"PV-Prognose {forecast_entity} nicht lesbar")

        soc_state = self.hass.state(entities.battery_soc) if entities.battery_soc else None
        soc = soc_state.as_float() if soc_state else None
        if entities.battery_soc and soc is None:
            notes.append("Ladezustand nicht lesbar, es wird von halb gefuelltem Speicher ausgegangen")

        return HistoryBundle(
            household_hourly=household,
            wallbox_hourly=wallbox,
            heatpump_hourly=heatpump,
            pv_forecast_state=pv_state,
            battery_soc_pct=soc,
            basis=basis,
            notes=notes,
        )

    # ---------------------------------------------------------------- Lauf

    def carry_forward_soc(
        self,
        settings: Settings,
        bundle: HistoryBundle,
        target_day: date,
        battery: Any = None,
    ) -> float | None:
        """Ladezustand vom Jetzt bis Mitternacht fortschreiben.

        Der Lauf fällt nachmittags an, bewertet aber den Folgetag. Zwischen
        beidem lädt die PV noch und der Abendverbrauch entlaedt wieder; ohne
        diese Fortschreibung startet die Rechnung mit einem falschen Füllstand.
        """
        if bundle.battery_soc_pct is None:
            return None
        battery = battery or settings.scenario_battery()
        now = datetime.now(tz=UTC)
        today = now.astimezone(self.tz).date()
        if target_day <= today:
            return battery.energy_above_min(bundle.battery_soc_pct)

        rest = [slot for slot in slot_starts_utc(today, self.tz) if slot >= now]
        if not rest:
            return battery.energy_above_min(bundle.battery_soc_pct)

        today_state = (
            self.hass.state(settings.entities.pv_forecast_today)
            if settings.entities.pv_forecast_today
            else None
        )
        rest_bundle = HistoryBundle(
            household_hourly=bundle.household_hourly,
            wallbox_hourly=bundle.wallbox_hourly,
            heatpump_hourly=bundle.heatpump_hourly,
            pv_forecast_state=today_state,
            battery_soc_pct=bundle.battery_soc_pct,
            basis=bundle.basis,
        )
        today_forecast = build_day_forecast(
            today, slot_starts_utc(today, self.tz), self.tz, settings, rest_bundle
        )
        offset = len(today_forecast.slots_utc) - len(rest)
        remaining = simulate_baseline(
            today_forecast.load_kwh[offset:],
            today_forecast.pv_kwh[offset:],
            battery,
            battery.energy_above_min(bundle.battery_soc_pct),
            SLOT_HOURS,
        )
        return remaining.soc_kwh[-1] if remaining.soc_kwh else None

    def evaluate(self, day: date, settings: Settings | None = None, bundle: HistoryBundle | None = None) -> DayEvaluation:
        settings = settings or self.settings_store.load()
        slots = slot_starts_utc(day, self.tz)
        series = self.prices.fetch_day(day, self.tz)
        spot = series.for_slots(slots)

        bundle = bundle if bundle is not None else self.collect_history(settings, day)
        forecast = build_day_forecast(day, slots, self.tz, settings, bundle)

        # Lade- und Entladegrenzen können live aus Home Assistant kommen; das BMS
        # senkt sie bei kalten Zellen ab, ein fester Wert wäre dann zu optimistisch.
        battery = settings.scenario_battery()
        if self.hass.configured:
            limits = resolve_limits(self.hass, battery, settings.entities)
            battery = apply_limits(battery, limits)
            forecast.notes.extend(limits.notes)
            forecast.notes.append(
                f"Grenzen: laden {limits.charge_kw:.1f} kW (DC), aus dem Netz "
                f"{limits.grid_charge_kw:.1f} kW, entladen {limits.discharge_kw:.1f} kW"
            )

        start_energy = self.carry_forward_soc(settings, bundle, day, battery)
        if start_energy is not None and day > datetime.now(tz=self.tz).date():
            forecast.notes.append(
                f"Ladezustand auf {start_energy:.1f} kWh zu Tagesbeginn fortgeschrieben"
            )
        scenario = "battery_60kwh" if settings.scenarios.battery_60kwh else "standard"
        return evaluate_day(
            forecast,
            spot,
            settings,
            self.tz,
            battery,
            bundle.battery_soc_pct,
            scenario=scenario,
            start_energy_kwh=start_energy,
        )

    def run_day(self, day: date, publish_states: bool = True) -> dict[str, Any]:
        """Einen Tag bewerten, speichern und optional als Sensoren bereitstellen."""
        settings = self.settings_store.load()
        run_id = self.store.start_run(day)
        try:
            evaluation = self.evaluate(day, settings)
        except PricesUnavailable as err:
            self.store.finish_run(run_id, "keine_preise", str(err))
            return {"status": "keine_preise", "message": str(err), "day": day.isoformat()}
        except Exception as err:  # unerwarteter Fehler soll den Dienst nicht beenden
            _LOG.exception("Bewertung für %s fehlgeschlagen", day)
            self.store.finish_run(run_id, "fehler", str(err))
            return {"status": "fehler", "message": str(err), "day": day.isoformat()}

        self.store.save_forecast(evaluation)
        published, weg = 0, "aus"
        if publish_states and settings.publish_sensors and self.hass.configured:
            published, weg = self.publish_result(settings, evaluation)

        message = (
            f"Ersparnis {evaluation.saving_vs_fixed_eur:.2f} EUR, "
            f"Speicher {evaluation.storage.verdict}, Entitäten über {weg}"
        )
        self.store.finish_run(run_id, "ok", message)
        return {
            "status": "ok",
            "day": day.isoformat(),
            "published": published,
            "evaluation": evaluation.as_dict(),
        }

    def broker(self) -> Any:
        """Zugangsdaten des MQTT-Brokers, einmal je Laufzeit ermittelt."""
        if not hasattr(self, "_broker"):
            self._broker = broker_from_supervisor(self.hass.token)
            if self._broker:
                _LOG.info("MQTT-Broker %s:%s gefunden", self._broker.host, self._broker.port)
        return self._broker

    def publish_result(self, settings: Settings, evaluation: DayEvaluation) -> tuple[int, str]:
        """Entitäten bereitstellen: bevorzugt über MQTT, sonst über die Zustands-API.

        MQTT-Discovery legt echte Entitäten an, die einen Neustart von Home
        Assistant überstehen; die Zustands-API ist der Notnagel ohne Broker.
        """
        modus = settings.sensor_mode
        if modus in {"auto", "mqtt"}:
            broker = self.broker()
            if broker is not None:
                try:
                    return MqttPublisher(broker).publish(evaluation), "MQTT"
                except Exception as err:
                    _LOG.warning("MQTT-Veröffentlichung fehlgeschlagen: %s", err)
                    if modus == "mqtt":
                        return 0, "MQTT (fehlgeschlagen)"
            elif modus == "mqtt":
                _LOG.warning("Kein MQTT-Broker verfügbar, keine Entitäten geschrieben")
                return 0, "MQTT (kein Broker)"
        if modus == "mqtt":
            return 0, "MQTT (fehlgeschlagen)"
        return publish_via_rest(self.hass, evaluation), "Zustands-API"

    def run_daily(self) -> dict[str, Any]:
        """Regellauf: Folgetag bewerten und offene Ist-Werte nachtragen."""
        today = datetime.now(tz=self.tz).date()
        result = self.run_day(today + timedelta(days=1))
        settings = self.settings_store.load()
        try:
            result["backfilled"] = backfill(
                self.tz, settings, self.hass, self.prices, self.store, today
            )
        except Exception as err:  # Nachtrag ist optional
            _LOG.warning("Ist-Abgleich fehlgeschlagen: %s", err)
            result["backfilled"] = []
        return result
