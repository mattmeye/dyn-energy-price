"""Weboberflaeche für das Ingress-Panel."""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .aggregate import conclusion, cumulative_savings, summarise
from .config import Settings
from .discovery import ROLES, discover
from .hass import HomeAssistantError
from .runner import Runner
from .scheduler import Scheduler

_LOG = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "web" / "static"
MAX_BODY_BYTES = 1_000_000


class Api:
    """Die Endpunkte, unabhaengig vom HTTP-Geruest gehalten."""

    def __init__(self, runner: Runner, scheduler: Scheduler | None) -> None:
        self.runner = runner
        self.scheduler = scheduler
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        settings = self.runner.settings_store.load()
        today = datetime.now(tz=self.runner.tz).date()
        latest = self.runner.store.get_forecast(today + timedelta(days=1)) or self.runner.store.get_forecast(today)
        return {
            "configured": settings.configured,
            "home_assistant": self.runner.hass.configured and self.runner.hass.ping(),
            "timezone": self.runner.options.timezone,
            "today": today.isoformat(),
            "latest_day": latest.day.isoformat() if latest else None,
            "scheduler": self.scheduler.status() if self.scheduler else {},
            "scenario_battery_60": settings.scenarios.battery_60kwh,
            "heatpump_enabled": settings.heatpump.enabled,
        }

    def entities(self) -> dict[str, Any]:
        try:
            states = self.runner.hass.states()
        except HomeAssistantError as err:
            return {"error": str(err), "roles": [], "entities": []}
        found = discover(states)
        roles = [
            {
                "key": role.key,
                "label": role.label,
                "hint": role.hint,
                "required": role.required,
                "candidates": [c.as_dict() for c in found.get(role.key, [])[:12]],
            }
            for role in ROLES
        ]
        listed = [
            {
                "entity_id": state.entity_id,
                "name": state.friendly_name,
                "unit": state.unit,
                "state": state.state,
            }
            for state in states
            if state.entity_id.startswith("sensor.")
        ]
        listed.sort(key=lambda item: item["entity_id"])
        return {"roles": roles, "entities": listed}

    def get_settings(self) -> dict[str, Any]:
        return self.runner.settings_store.load().to_dict()

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = Settings.from_dict(payload)
        settings.configured = True
        self.runner.settings_store.save(settings)
        # Nach der Ersteinrichtung läuft der Zeitplan sofort an.
        if self.scheduler is not None and not self.scheduler.is_alive():
            self.scheduler.start()
        return {"status": "ok", "settings": settings.to_dict()}

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        day_text = payload.get("day")
        if day_text:
            day = date.fromisoformat(str(day_text))
        else:
            day = datetime.now(tz=self.runner.tz).date() + timedelta(days=1)
        with self._lock:  # zwei gleichzeitige Läufe würden sich nur behindern
            return self.runner.run_day(day)

    def forecast(self, params: dict[str, list[str]]) -> dict[str, Any]:
        day_text = (params.get("day") or [""])[0]
        if day_text:
            day = date.fromisoformat(day_text)
        else:
            today = datetime.now(tz=self.runner.tz).date()
            row = self.runner.store.get_forecast(today + timedelta(days=1))
            day = row.day if row else today
        scenario = (params.get("scenario") or ["standard"])[0]
        row = self.runner.store.get_forecast(day, scenario)
        if row is None:
            return {"status": "leer", "day": day.isoformat()}
        actual = self.runner.store.get_actual(day)
        return {
            "status": "ok",
            "forecast": row.payload,
            "actual": actual.payload if actual else None,
        }

    def history(self, params: dict[str, list[str]]) -> dict[str, Any]:
        days = int((params.get("days") or ["120"])[0])
        start = datetime.now(tz=self.runner.tz).date() + timedelta(days=1 - max(1, days))
        # Ohne obere Grenze, damit auch von Hand gerechnete spätere Tage auftauchen.
        forecasts = self.runner.store.list_forecasts(start, None)
        actuals = self.runner.store.list_actuals(start, None)
        return {
            "days": cumulative_savings(forecasts, actuals),
            "months": [s.as_dict() for s in summarise(forecasts, actuals, "month")],
            "years": [s.as_dict() for s in summarise(forecasts, actuals, "year")],
            "conclusion": conclusion(summarise(forecasts, actuals, "month")),
        }

    def runs(self) -> dict[str, Any]:
        return {"runs": self.runner.store.recent_runs(30)}


class Handler(BaseHTTPRequestHandler):
    server_version = "naturstrom-smart"
    api: Api

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - Signatur vorgegeben
        _LOG.debug("%s %s", self.address_string(), fmt % args)

    # ----------------------------------------------------------- Antworten

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._json({"error": "nicht gefunden"}, 404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("Anfrage zu groß")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise ValueError("Ungültiges JSON") from err
        return data if isinstance(data, dict) else {}

    # -------------------------------------------------------------- Routen

    def do_GET(self) -> None:  # noqa: N802 - Signatur vorgegeben
        parsed = urlparse(self.path)
        path = parsed.path.strip("/")
        params = parse_qs(parsed.query)
        routes: dict[str, Callable[[], Any]] = {
            "api/status": self.api.status,
            "api/entities": self.api.entities,
            "api/settings": self.api.get_settings,
            "api/forecast": lambda: self.api.forecast(params),
            "api/history": lambda: self.api.history(params),
            "api/runs": self.api.runs,
        }
        if path in routes:
            try:
                self._json(routes[path]())
            except Exception as err:
                _LOG.exception("Fehler in %s", path)
                self._json({"error": str(err)}, 500)
            return
        if path in {"", "index.html"}:
            self._static("index.html")
            return
        self._static(path)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.strip("/")
        try:
            payload = self._body()
        except ValueError as err:
            self._json({"error": str(err)}, 400)
            return
        try:
            if path == "api/settings":
                self._json(self.api.save_settings(payload))
            elif path == "api/run":
                self._json(self.api.run(payload))
            else:
                self._json({"error": "nicht gefunden"}, 404)
        except Exception as err:
            _LOG.exception("Fehler in %s", path)
            self._json({"error": str(err)}, 500)


def serve(runner: Runner, scheduler: Scheduler | None, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"api": Api(runner, scheduler)})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)  # noqa: S104 - nur über Ingress erreichbar
    thread = threading.Thread(target=server.serve_forever, name="ns-web", daemon=True)
    thread.start()
    _LOG.info("Weboberflaeche läuft auf Port %s", port)
    return server
