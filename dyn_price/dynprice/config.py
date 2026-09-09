"""Konfiguration: Add-on-Optionen (/data/options.json) und UI-Einstellungen (/data/settings.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

# Referenz-Netzbezug der letzten zwoelf Monate (kWh je Monat, 1 = Januar).
# Dient nur der Plausibilisierung der aus der Historie abgeleiteten Prognose.
DEFAULT_MONTHLY_REFERENCE_KWH: dict[str, float] = {
    "1": 380.0,
    "2": 303.0,
    "3": 48.0,
    "4": 19.0,
    "5": 15.0,
    "6": 15.0,
    "7": 15.0,
    "8": 20.0,
    "9": 188.0,
    "10": 238.0,
    "11": 370.0,
    "12": 380.0,
}

# Typisches Monatsprofil einer Luft-Wasser-Wärmepumpe (Anteil am Jahresverbrauch).
DEFAULT_HEATPUMP_MONTHLY_SHARE: list[float] = [
    0.170, 0.150, 0.115, 0.070, 0.035, 0.020,
    0.015, 0.015, 0.030, 0.075, 0.130, 0.175,
]


@dataclass
class Entities:
    """Entity-IDs aus Home Assistant. Leer = nicht konfiguriert."""

    grid_import: str = ""            # Zähler Netzbezug, kWh, total_increasing
    grid_export: str = ""            # Einspeisung, kWh (optional)
    house_consumption: str = ""      # Hausverbrauch, kWh (optional, bevorzugt)
    pv_production: str = ""          # PV-Erzeugung, kWh (optional)
    pv_forecast_tomorrow: str = ""   # Forecast.Solar: Prognose Folgetag, kWh
    pv_forecast_today: str = ""      # Forecast.Solar: Prognose heute, kWh
    battery_soc: str = ""            # Ladezustand, %
    battery_charge_energy: str = ""  # Speicher-Ladung, kWh (optional)
    battery_discharge_energy: str = ""  # Speicher-Entladung, kWh (optional)
    wallbox_energy: str = ""         # Wallbox-Ladeenergie, kWh (optional)
    heatpump_energy: str = ""        # Wärmepumpe, kWh (optional)
    # Grenzen, die das System selbst meldet. Victron gibt sie in Ampere aus;
    # zusammen mit der Batteriespannung wird daraus eine Leistung.
    battery_charge_limit: str = ""   # DVCC Ladestromgrenze (CCL)
    battery_discharge_limit: str = ""  # DVCC Entladestromgrenze (DCL)
    battery_voltage: str = ""        # Batteriespannung, V
    battery_soc_min: str = ""        # ESS-Mindest-SoC, %
    ac_input_limit: str = ""         # Eingangsstrombegrenzung, A


@dataclass
class Battery:
    """Speicherparameter. Vorbelegt mit der bekannten 30-kWh-Anlage."""

    nominal_kwh: float = 30.0
    soc_min_pct: float = 10.0
    soc_max_pct: float = 100.0
    usable_kwh_override: float = 0.0   # 0 = aus Nennkapazität und SoC-Grenzen ableiten

    # Wechselrichter/Ladegeräte. Vorbelegt mit 3 x MultiPlus-II 48/5000/70:
    # der Ladestrom steht auf dem Typenschild hinter dem zweiten Schrägstrich.
    charger_count: int = 3
    charger_current_a: float = 70.0
    # 0 = Netzladeleistung aus Anzahl, Ladestrom und Batteriespannung ableiten.
    grid_charge_kw_override: float = 0.0
    # 0 = gleichstromseitige Ladeannahme aus PV-Weg und Ladegeräten ableiten.
    # Wird gesetzt, wenn das BMS eine Ladestromgrenze meldet.
    charge_kw_override: float = 0.0

    # Wie die PV angebunden ist: "ac" über einen eigenen Wechselrichter, "dc"
    # über MPPT-Regler direkt am Speicher. Bestimmt, ob die PV-Ladung durch die
    # Ladegeräte läuft - und damit Leistung und Wirkungsgrad dieses Weges.
    # Die Anlage ist AC-gekoppelt: PV und Netz teilen sich dieselben Ladegeräte.
    pv_coupling: str = "ac"
    mppt_charge_kw: float = 10.0      # Summe der MPPT-Regler, nur bei "dc"

    # Dauer-Ausgangsleistung aller Geräte zusammen. MultiPlus-II 48/5000:
    # 4000 W je Gerät bei 25 Grad, also 12 kW zu dritt.
    discharge_kw: float = 12.0

    # Ersatzwert, wenn keine Spannungs-Entität gewählt ist (16s LFP).
    nominal_voltage_v: float = 51.2
    # Eingangsstrombegrenzung des Wechselrichters. Begrenzt zusätzlich, was aus
    # dem Netz durch das Gerät fließen kann. 0 = keine Begrenzung.
    ac_input_limit_a: float = 0.0
    mains_voltage_v: float = 230.0
    phases: int = 3
    # Wirkungsgrad in drei Stufen statt einer Zahl: nur die erste ist aus den
    # eigenen Zählern messbar, die beiden anderen sind Geräteeigenschaften.
    # Der Standby der Geräte gehört in keine davon - er steckt bereits im
    # gemessenen Verbrauchsprofil und wäre hier doppelt gezählt.
    battery_dc_efficiency: float = 0.97    # Zellen, Gleichstromseite
    charger_efficiency: float = 0.93       # AC -> DC im Ladegerät
    inverter_efficiency: float = 0.94      # DC -> AC im Wechselrichter
    grid_charge_controllable: bool = False

    @property
    def usable_kwh(self) -> float:
        if self.usable_kwh_override > 0:
            return self.usable_kwh_override
        span = max(0.0, self.soc_max_pct - self.soc_min_pct) / 100.0
        return self.nominal_kwh * span

    @property
    def grid_charge_kw(self) -> float:
        """Was aus dem Netz über die Ladegeräte in den Speicher geht."""
        if self.grid_charge_kw_override > 0:
            return self.grid_charge_kw_override
        return max(1, self.charger_count) * self.charger_current_a * self.nominal_voltage_v / 1000.0

    @property
    def pv_charge_kw(self) -> float:
        """Was die PV in den Speicher bringt.

        DC-gekoppelt über die MPPT-Regler, AC-gekoppelt durch die Ladegeräte -
        nie mehr, als der Speicher insgesamt annimmt.
        """
        if self.pv_coupling == "ac":
            return min(self.grid_charge_kw, self.charge_kw)
        return min(self.mppt_charge_kw, self.charge_kw)

    @property
    def charge_kw(self) -> float:
        """Was der Speicher gleichstromseitig insgesamt annimmt.

        Bei DC-Kopplung können MPPT-Regler und Ladegeräte gleichzeitig liefern;
        bei AC-Kopplung teilen sich beide Wege dieselben Ladegeräte.
        """
        if self.charge_kw_override > 0:
            return self.charge_kw_override
        if self.pv_coupling == "ac":
            return self.grid_charge_kw
        return self.mppt_charge_kw + self.grid_charge_kw

    @property
    def _half_dc(self) -> float:
        return max(0.01, self.battery_dc_efficiency) ** 0.5

    @property
    def pv_charge_efficiency(self) -> float:
        """PV in den Speicher. DC-gekoppelt ohne, AC-gekoppelt mit Ladegerät."""
        if self.pv_coupling == "ac":
            return self._half_dc * self.charger_efficiency
        return self._half_dc

    @property
    def grid_charge_efficiency(self) -> float:
        return self._half_dc * self.charger_efficiency

    @property
    def discharge_efficiency(self) -> float:
        return self._half_dc * self.inverter_efficiency

    @property
    def roundtrip_efficiency(self) -> float:
        """Netz -> Speicher -> Haus, der für die Verschiebung maßgebliche Weg."""
        return self.grid_charge_efficiency * self.discharge_efficiency

    def energy_above_min(self, soc_pct: float) -> float:
        """Nutzbare Energie im Speicher oberhalb der unteren SoC-Grenze."""
        usable_span = max(0.0, self.soc_max_pct - self.soc_min_pct)
        if usable_span <= 0:
            return 0.0
        rel = (min(soc_pct, self.soc_max_pct) - self.soc_min_pct) / usable_span
        return max(0.0, min(1.0, rel)) * self.usable_kwh


@dataclass
class ElectricVehicle:
    enabled: bool = True
    kwh_per_night: float = 0.0        # 0 = aus Wallbox-Historie ableiten
    power_kw: float = 11.0
    window_start_hour: int = 22
    window_end_hour: int = 6


@dataclass
class HeatPump:
    """Optionales Szenario, standardmaessig aus."""

    enabled: bool = False
    annual_kwh: float = 0.0
    monthly_share: list[float] = field(default_factory=lambda: list(DEFAULT_HEATPUMP_MONTHLY_SHARE))
    shiftable_share: float = 0.5


@dataclass
class Tariff:
    """Dynamischer Tarif gegen Fixtarif, alle Werte brutto.

    Vorbelegt mit den Werten eines dynamischen Tarifs nach Tarifblatt Stand
    September 2026; alle Bestandteile sind in der Einrichtung änderbar.
    """

    grid_and_levies_ct: float = 15.29   # Netz, Abgaben, Umlagen, Steuern
    service_ct: float = 1.19            # Servicepauschale
    vat_factor: float = 1.19            # Umsatzsteuer auf den Börsenpreis netto
    base_price_eur_month: float = 15.74
    fixed_price_ct: float = 31.0
    fixed_base_price_eur_month: float = 0.0

    def all_in_ct(self, spot_eur_mwh: float) -> float:
        """All-in-Arbeitspreis brutto in ct/kWh aus dem Börsenpreis netto in EUR/MWh."""
        spot_net_ct_per_kwh = spot_eur_mwh / 10.0
        return self.grid_and_levies_ct + self.service_ct + spot_net_ct_per_kwh * self.vat_factor


@dataclass
class ForecastSettings:
    history_days: int = 28
    min_history_days: int = 7
    monthly_reference_kwh: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_MONTHLY_REFERENCE_KWH)
    )
    reference_weight: float = 0.5   # Gewicht der Monatsreferenz gegen die Historie
    baseload_kw: float = 0.0        # 0 = aus Historie ableiten
    pv_latitude: float = 51.0       # nur für die Tagesform der PV-Prognose
    pv_peak_hour: float = 13.0      # lokale Stunde des Erzeugungsmaximums


@dataclass
class Scenarios:
    battery_60kwh: bool = False


@dataclass
class Settings:
    configured: bool = False
    entities: Entities = field(default_factory=Entities)
    battery: Battery = field(default_factory=Battery)
    ev: ElectricVehicle = field(default_factory=ElectricVehicle)
    heatpump: HeatPump = field(default_factory=HeatPump)
    tariff: Tariff = field(default_factory=Tariff)
    forecast: ForecastSettings = field(default_factory=ForecastSettings)
    scenarios: Scenarios = field(default_factory=Scenarios)
    publish_sensors: bool = True
    # "auto" nimmt MQTT, wenn ein Broker bereitsteht, sonst die Zustands-API.
    # "mqtt" erzwingt MQTT, "rest" erzwingt die Zustands-API.
    sensor_mode: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        return _build(cls, data or {})

    def scenario_battery(self) -> Battery:
        """Speicher im Szenario 60 kWh (sonst unveraendert)."""
        if not self.scenarios.battery_60kwh:
            return self.battery
        scaled = Battery(**asdict(self.battery))
        scaled.nominal_kwh = self.battery.nominal_kwh * 2
        if self.battery.usable_kwh_override > 0:
            scaled.usable_kwh_override = self.battery.usable_kwh_override * 2
        return scaled


def _coerce(current: Any, value: Any) -> Any:
    """Bringt einen JSON-Wert auf den Typ des vorhandenen Standardwerts."""
    if value is None:
        return current
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "ja"}
        return bool(value)
    if isinstance(current, float):
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return current
    if isinstance(current, int):
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return current
    if isinstance(current, str):
        return str(value)
    return value


def _build(cls: type, data: dict[str, Any]) -> Any:
    """Baut eine Dataclass aus einem Dict; unbekannte Schluessel werden ignoriert."""
    obj = cls()
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(obj, f.name)
        if is_dataclass(current) and isinstance(value, dict):
            setattr(obj, f.name, _build(type(current), value))
        elif isinstance(current, dict) and isinstance(value, dict):
            setattr(obj, f.name, {str(k): v for k, v in value.items()})
        elif isinstance(current, list) and isinstance(value, list):
            setattr(obj, f.name, list(value))
        else:
            setattr(obj, f.name, _coerce(current, value))
    return obj


@dataclass
class AddonOptions:
    """Aus den Add-on-Optionen bzw. Umgebungsvariablen."""

    log_level: str = "info"
    run_hour: int = 13
    run_minute: int = 30
    timezone: str = "Europe/Berlin"
    price_source: str = "energy-charts"
    data_dir: Path = Path("/data")
    port: int = 8099

    @classmethod
    def from_env(cls) -> "AddonOptions":
        opts = cls()
        options_file = Path(os.environ.get("DP_OPTIONS_FILE", "/data/options.json"))
        if options_file.is_file():
            try:
                raw = json.loads(options_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            opts.log_level = str(raw.get("log_level", opts.log_level))
            opts.run_hour = int(raw.get("run_hour", opts.run_hour))
            opts.run_minute = int(raw.get("run_minute", opts.run_minute))
            opts.timezone = str(raw.get("timezone", opts.timezone))
            opts.price_source = str(raw.get("price_source", opts.price_source))
        # Umgebungsvariablen haben Vorrang (run.sh setzt sie aus bashio).
        opts.log_level = os.environ.get("DP_LOG_LEVEL", opts.log_level)
        opts.run_hour = int(os.environ.get("DP_RUN_HOUR", opts.run_hour))
        opts.run_minute = int(os.environ.get("DP_RUN_MINUTE", opts.run_minute))
        opts.timezone = os.environ.get("DP_TIMEZONE", opts.timezone)
        opts.price_source = os.environ.get("DP_PRICE_SOURCE", opts.price_source)
        opts.data_dir = Path(os.environ.get("DP_DATA_DIR", str(opts.data_dir)))
        opts.port = int(os.environ.get("DP_PORT", opts.port))
        return opts


class SettingsStore:
    """Persistiert die UI-Einstellungen als JSON neben der Datenbank."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Settings:
        if not self.path.is_file():
            return Settings()
        try:
            return Settings.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError):
            return Settings()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
