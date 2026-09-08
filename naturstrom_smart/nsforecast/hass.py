"""Zugriff auf Home Assistant: REST über den Supervisor-Proxy und Langzeitstatistik per WebSocket."""

from __future__ import annotations

import json
import logging
import os
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .timeutil import UTC, iso_utc, parse_iso

_LOG = logging.getLogger(__name__)

DEFAULT_REST_URL = "http://supervisor/core/api"
DEFAULT_WS_URL = "ws://supervisor/core/websocket"


class HomeAssistantError(RuntimeError):
    """Home Assistant nicht erreichbar oder Anfrage abgelehnt."""


@dataclass(frozen=True)
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_updated: datetime | None = None

    @property
    def unit(self) -> str:
        return str(self.attributes.get("unit_of_measurement", ""))

    @property
    def device_class(self) -> str:
        return str(self.attributes.get("device_class", ""))

    @property
    def state_class(self) -> str:
        return str(self.attributes.get("state_class", ""))

    @property
    def friendly_name(self) -> str:
        return str(self.attributes.get("friendly_name", self.entity_id))

    def as_float(self) -> float | None:
        try:
            return float(str(self.state).replace(",", "."))
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class StatPoint:
    start_utc: datetime
    value: float | None       # Zählerstand (sum) am Intervallbeginn
    mean: float | None = None


class HomeAssistant:
    """Dünner Client für die Kern-API. Im Add-on genuegt der Supervisor-Token."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        ws_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("NS_HA_URL") or DEFAULT_REST_URL).rstrip("/")
        self.token = token or os.environ.get("NS_HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN") or ""
        self.ws_url = ws_url or os.environ.get("NS_HA_WS_URL") or self._derive_ws_url()
        self.timeout = timeout

    def _derive_ws_url(self) -> str:
        if self.base_url == DEFAULT_REST_URL:
            return DEFAULT_WS_URL
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        without_scheme = self.base_url.split("://", 1)[-1]
        return f"{scheme}://{without_scheme}/websocket"

    @property
    def configured(self) -> bool:
        return bool(self.token)

    # ---------------------------------------------------------------- REST

    def _request(self, method: str, path: str, params: dict | None = None, body: Any = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - konfigurierte URL
                payload = response.read().decode("utf-8")
        except HTTPError as err:
            raise HomeAssistantError(f"{method} {path} -> HTTP {err.code}") from err
        except (URLError, TimeoutError, ssl.SSLError) as err:
            raise HomeAssistantError(f"{method} {path} nicht erreichbar: {err}") from err
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as err:
            raise HomeAssistantError(f"{method} {path} lieferte kein JSON") from err

    def ping(self) -> bool:
        try:
            self._request("GET", "/")
        except HomeAssistantError:
            return False
        return True

    def states(self) -> list[EntityState]:
        raw = self._request("GET", "/states") or []
        out: list[EntityState] = []
        for item in raw:
            try:
                last = parse_iso(item["last_updated"]) if item.get("last_updated") else None
            except ValueError:
                last = None
            out.append(
                EntityState(
                    entity_id=item.get("entity_id", ""),
                    state=str(item.get("state", "")),
                    attributes=item.get("attributes") or {},
                    last_updated=last,
                )
            )
        return out

    def state(self, entity_id: str) -> EntityState | None:
        if not entity_id:
            return None
        try:
            item = self._request("GET", f"/states/{entity_id}")
        except HomeAssistantError:
            return None
        if not item:
            return None
        return EntityState(
            entity_id=item.get("entity_id", entity_id),
            state=str(item.get("state", "")),
            attributes=item.get("attributes") or {},
        )

    def set_state(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> None:
        self._request("POST", f"/states/{entity_id}", body={"state": state, "attributes": attributes or {}})

    def history(self, entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Zustandsverlauf aus dem Recorder (nur innerhalb der Aufbewahrungsfrist)."""
        params = {
            "filter_entity_id": entity_id,
            "end_time": iso_utc(end),
            "minimal_response": "true",
            "significant_changes_only": "false",
        }
        raw = self._request("GET", f"/history/period/{iso_utc(start)}", params=params) or []
        out: list[tuple[datetime, float]] = []
        for series in raw:
            for item in series:
                value = item.get("state")
                stamp = item.get("last_changed") or item.get("last_updated")
                if value in (None, "", "unknown", "unavailable") or not stamp:
                    continue
                try:
                    out.append((parse_iso(stamp).astimezone(UTC), float(str(value).replace(",", "."))))
                except ValueError:
                    continue
        out.sort(key=lambda row: row[0])
        return out

    # ----------------------------------------------------------- WebSocket

    def statistics(
        self,
        entity_ids: Sequence[str],
        start: datetime,
        end: datetime,
        period: str = "hour",
    ) -> dict[str, list[StatPoint]]:
        """Langzeitstatistik (stündlich). Fällt bei Problemen auf {} zurück."""
        wanted = [e for e in entity_ids if e]
        if not wanted:
            return {}
        try:
            import websocket  # type: ignore
        except ImportError:
            _LOG.warning("websocket-client fehlt; Langzeitstatistik nicht verfügbar")
            return {}

        message = {
            "type": "recorder/statistics_during_period",
            "start_time": iso_utc(start),
            "end_time": iso_utc(end),
            "statistic_ids": list(wanted),
            "period": period,
            "types": ["sum", "state", "mean"],
        }
        try:
            raw = self._ws_call(websocket, message)
        except Exception as err:  # Verbindungs- oder Protokollfehler
            _LOG.warning("Langzeitstatistik nicht abrufbar: %s", err)
            return {}

        out: dict[str, list[StatPoint]] = {}
        for entity_id, rows in (raw or {}).items():
            points: list[StatPoint] = []
            for row in rows:
                moment = _stat_start(row.get("start"))
                if moment is None:
                    continue
                value = row.get("sum")
                if value is None:
                    value = row.get("state")
                points.append(
                    StatPoint(
                        start_utc=moment,
                        value=None if value is None else float(value),
                        mean=None if row.get("mean") is None else float(row["mean"]),
                    )
                )
            points.sort(key=lambda p: p.start_utc)
            out[entity_id] = points
        return out

    def _ws_call(self, websocket_module: Any, message: dict) -> Any:
        connection = websocket_module.create_connection(self.ws_url, timeout=self.timeout)
        try:
            greeting = json.loads(connection.recv())
            if greeting.get("type") == "auth_required":
                connection.send(json.dumps({"type": "auth", "access_token": self.token}))
                auth = json.loads(connection.recv())
                if auth.get("type") != "auth_ok":
                    raise HomeAssistantError("WebSocket-Anmeldung abgelehnt")
            payload = dict(message)
            payload["id"] = 1
            connection.send(json.dumps(payload))
            while True:
                response = json.loads(connection.recv())
                if response.get("id") != 1:
                    continue
                if not response.get("success", False):
                    raise HomeAssistantError(str(response.get("error", "unbekannter Fehler")))
                return response.get("result")
        finally:
            try:
                connection.close()
            except Exception:  # Verbindungsabbau darf den Lauf nicht stoeren
                pass


def _stat_start(value: Any) -> datetime | None:
    """Statistik-Zeitstempel: je nach HA-Version ISO-String oder Millisekunden."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    try:
        return parse_iso(str(value)).astimezone(UTC)
    except ValueError:
        return None


def counter_to_hourly(points: Iterable[StatPoint]) -> list[tuple[datetime, float]]:
    """Aus Zaehlerstaenden (total_increasing) den Verbrauch je Intervall bilden."""
    rows = [p for p in points if p.value is not None]
    out: list[tuple[datetime, float]] = []
    for previous, current in zip(rows, rows[1:]):
        delta = float(current.value) - float(previous.value)
        if delta < 0:
            continue  # Zählerüberlauf oder Rücksetzung: Intervall verwerfen
        out.append((previous.start_utc, delta))
    return out
