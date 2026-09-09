"""Taeglicher Lauf, sobald die Preise für den Folgetag vorliegen."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta
from typing import Any

from .runner import Runner

_LOG = logging.getLogger(__name__)

RETRY_MINUTES = 20
LAST_ATTEMPT_HOUR = 23


class Scheduler(threading.Thread):
    """Prüft jede Minute, ob der Lauf für den Folgetag fällig ist.

    Die Day-Ahead-Preise stehen ueblicherweise ab etwa 13 Uhr bereit. Fehlen sie
    noch, wird in Abständen erneut versucht, bis der Tag zu Ende geht.
    """

    def __init__(self, runner: Runner, stop_event: threading.Event) -> None:
        super().__init__(name="dp-scheduler", daemon=True)
        self.runner = runner
        self.stop_event = stop_event
        self.last_success: date | None = None
        self.last_attempt: datetime | None = None
        self.last_result: dict[str, Any] | None = None

    # -------------------------------------------------------------- Ablauf

    def startup(self) -> None:
        """Nach dem Start einmal rechnen, damit die Sensoren sofort existieren."""
        today = datetime.now(tz=self.runner.tz).date()
        result = self.runner.run_day(today + timedelta(days=1))
        if result.get("status") != "ok":
            _LOG.info("Folgetag noch nicht bewertbar (%s), bewerte heute", result.get("status"))
            result = self.runner.run_day(today)
        else:
            self.last_success = today
        self.last_result = result

    def due(self, now: datetime) -> bool:
        if self.last_success == now.date():
            return False
        run_at = time(hour=self.runner.options.run_hour, minute=self.runner.options.run_minute)
        if now.time() < run_at:
            return False
        if now.hour >= LAST_ATTEMPT_HOUR and self.last_attempt is not None:
            return False
        if self.last_attempt is not None and now - self.last_attempt < timedelta(minutes=RETRY_MINUTES):
            return False
        return True

    def run_once(self, now: datetime) -> dict[str, Any]:
        self.last_attempt = now
        result = self.runner.run_daily()
        self.last_result = result
        if result.get("status") == "ok":
            self.last_success = now.date()
            _LOG.info("Lauf für %s erfolgreich", result.get("day"))
        else:
            _LOG.info("Lauf verschoben: %s", result.get("message"))
        return result

    def run(self) -> None:
        try:
            self.startup()
        except Exception:  # ein fehlgeschlagener Startlauf darf den Dienst nicht beenden
            _LOG.exception("Startlauf fehlgeschlagen")
        while not self.stop_event.is_set():
            now = datetime.now(tz=self.runner.tz)
            try:
                if self.due(now):
                    self.run_once(now)
            except Exception:
                _LOG.exception("Geplanter Lauf fehlgeschlagen")
            self.stop_event.wait(60)

    def status(self) -> dict[str, Any]:
        return {
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "next_run_at": f"{self.runner.options.run_hour:02d}:{self.runner.options.run_minute:02d}",
            "last_status": (self.last_result or {}).get("status"),
            "last_message": (self.last_result or {}).get("message"),
        }
