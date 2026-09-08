"""Day-Ahead-Börsenpreise DE-LU von api.energy-charts.info, mit lokalem Cache."""

from __future__ import annotations

import bisect
import csv
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .timeutil import UTC, day_bounds_utc, parse_iso

_LOG = logging.getLogger(__name__)

API_URL = "https://api.energy-charts.info/price"
BIDDING_ZONE = "DE-LU"
USER_AGENT = "naturstrom-smart-addon/0.1 (Home Assistant)"


class PricesUnavailable(RuntimeError):
    """Für den gewuenschten Tag liegen keine vollständigen Preise vor."""


@dataclass(frozen=True)
class PricePoint:
    start_utc: datetime
    spot_eur_mwh: float


class PriceSeries:
    """Zeitreihe von Börsenpreisen als Treppenfunktion."""

    def __init__(self, points: Iterable[PricePoint]) -> None:
        self._points = sorted(points, key=lambda p: p.start_utc)
        self._starts = [p.start_utc for p in self._points]

    def __len__(self) -> int:
        return len(self._points)

    def __iter__(self):
        return iter(self._points)

    @property
    def points(self) -> list[PricePoint]:
        return list(self._points)

    @property
    def resolution_minutes(self) -> int | None:
        if len(self._points) < 2:
            return None
        deltas = [
            int((b.start_utc - a.start_utc).total_seconds() // 60)
            for a, b in zip(self._points, self._points[1:])
        ]
        return min(d for d in deltas if d > 0) if any(d > 0 for d in deltas) else None

    def at(self, moment: datetime) -> float | None:
        """Preis, der zum Zeitpunkt gilt (letzter Stützpunkt <= moment)."""
        if not self._points:
            return None
        idx = bisect.bisect_right(self._starts, moment) - 1
        if idx < 0:
            return None
        point = self._points[idx]
        # Nicht über eine Lücke hinaus extrapolieren.
        step = self.resolution_minutes or 60
        if moment - point.start_utc >= timedelta(minutes=step * 2):
            return None
        return point.spot_eur_mwh

    def for_slots(self, slot_starts: Sequence[datetime]) -> list[float]:
        values: list[float] = []
        for slot in slot_starts:
            value = self.at(slot)
            if value is None:
                raise PricesUnavailable(f"Kein Börsenpreis für {slot.isoformat()}")
            values.append(value)
        return values

    def covers(self, slot_starts: Sequence[datetime]) -> bool:
        try:
            self.for_slots(slot_starts)
        except PricesUnavailable:
            return False
        return True

    def merge(self, other: "PriceSeries") -> "PriceSeries":
        known = {p.start_utc: p for p in self._points}
        known.update({p.start_utc: p for p in other._points})
        return PriceSeries(known.values())


def _parse_energy_charts(payload: dict) -> PriceSeries:
    seconds = payload.get("unix_seconds") or []
    prices = payload.get("price") or []
    unit = str(payload.get("unit", "EUR/MWh")).upper()
    if len(seconds) != len(prices):
        raise PricesUnavailable("Antwort von energy-charts ist inkonsistent")
    factor = 1.0
    if unit in {"EUR/KWH", "€/KWH"}:
        factor = 1000.0
    elif unit in {"CT/KWH", "CENT/KWH"}:
        factor = 10.0
    points = [
        PricePoint(datetime.fromtimestamp(int(sec), tz=UTC), float(value) * factor)
        for sec, value in zip(seconds, prices)
        if value is not None
    ]
    if not points:
        raise PricesUnavailable("energy-charts lieferte keine Preise")
    return PriceSeries(points)


class PriceProvider:
    """Holt Preise von energy-charts und legt die Rohantwort im Cache ab.

    Der Cache macht Läufe offline wiederholbar: liegt die Antwort bereits
    lokal, wird nicht erneut angefragt.
    """

    def __init__(self, cache_dir: Path, offline: bool = False, timeout: float = 30.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.timeout = timeout

    def _cache_file(self, start: date, end: date) -> Path:
        return self.cache_dir / f"energy-charts_{BIDDING_ZONE}_{start.isoformat()}_{end.isoformat()}.json"

    def _read_cache(self, start: date, end: date) -> dict | None:
        path = self._cache_file(start, end)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, start: date, end: date, payload: dict) -> None:
        path = self._cache_file(start, end)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as err:  # Cache ist optional
            _LOG.warning("Preis-Cache nicht schreibbar: %s", err)

    def _download(self, start: date, end: date) -> dict:
        query = urlencode({"bzn": BIDDING_ZONE, "start": start.isoformat(), "end": end.isoformat()})
        request = Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - feste HTTPS-URL
            return json.loads(response.read().decode("utf-8"))

    def fetch_day(self, day: date, tz: ZoneInfo) -> PriceSeries:
        """Preise für den lokalen Kalendertag, notfalls aus dem Cache."""
        start_utc, end_utc = day_bounds_utc(day, tz)
        # Randtage mitnehmen, damit die UTC-Grenzen des lokalen Tages abgedeckt sind.
        start_day = (start_utc - timedelta(hours=2)).date()
        end_day = (end_utc + timedelta(hours=2)).date()

        payload = self._read_cache(start_day, end_day)
        if payload is None:
            if self.offline:
                raise PricesUnavailable(
                    f"Offline-Modus: keine zwischengespeicherten Preise für {day.isoformat()}"
                )
            try:
                payload = self._download(start_day, end_day)
            except PricesUnavailable:
                raise
            except Exception as err:  # Netzfehler, HTTP-Fehler, ungueltiges JSON
                raise PricesUnavailable(f"Abruf bei energy-charts fehlgeschlagen: {err}") from err
            self._write_cache(start_day, end_day, payload)

        series = _parse_energy_charts(payload)
        window = [p for p in series if start_utc <= p.start_utc < end_utc]
        if not window:
            raise PricesUnavailable(f"energy-charts kennt {day.isoformat()} noch nicht")
        # Auch den Stützpunkt vor Tagesbeginn behalten, damit .at() den ersten Slot trifft.
        before = [p for p in series if p.start_utc < start_utc]
        return PriceSeries(window + before[-1:])


def load_csv(path: Path) -> PriceSeries:
    """Preise aus einer lokalen CSV lesen: Spalten Zeitstempel und EUR/MWh."""
    points: list[PricePoint] = []
    text = Path(path).read_text(encoding="utf-8")
    # Deutsche Exporte trennen mit Semikolon und schreiben Dezimalkommas.
    delimiter = ";" if text.count(";") > text.count(",") else ","
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                moment = parse_iso(row[0])
                value = float(row[1].replace(",", "."))
            except ValueError:
                continue  # Kopfzeile oder unbrauchbare Zeile
            points.append(PricePoint(moment.astimezone(UTC), value))
    if not points:
        raise PricesUnavailable(f"Keine Preise in {path}")
    return PriceSeries(points)
