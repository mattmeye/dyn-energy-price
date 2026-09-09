"""Einstiegspunkt des Add-ons."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from .config import AddonOptions, SettingsStore
from .hass import HomeAssistant
from .prices import PriceProvider
from .runner import Runner
from .scheduler import Scheduler
from .store import Store
from .web import serve

_LOG = logging.getLogger("dynprice")


def main() -> int:
    options = AddonOptions.from_env()
    logging.basicConfig(
        level=getattr(logging, options.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    runner = Runner(
        options=options,
        settings_store=SettingsStore(options.data_dir / "settings.json"),
        store=Store(options.data_dir / "dynprice.db"),
        hass=HomeAssistant(),
        prices=PriceProvider(options.data_dir / "cache"),
    )

    stop_event = threading.Event()
    settings = runner.settings_store.load()
    scheduler = Scheduler(runner, stop_event)
    if settings.configured:
        scheduler.start()
    else:
        _LOG.warning("Noch nicht eingerichtet: Entitäten im Ingress-Panel auswaehlen")

    server = serve(runner, scheduler, options.port)

    def shutdown(*_: object) -> None:
        _LOG.info("Beende Add-on")
        stop_event.set()
        # Abmelden, damit die Entitäten in Home Assistant als nicht verfügbar
        # erscheinen statt veraltete Werte zu zeigen.
        runner.close()
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    stop_event.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
