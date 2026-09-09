"""Kommandozeile für Läufe ohne Add-on-Umgebung und für Offline-Wiederholungen."""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .aggregate import conclusion, summarise
from .config import AddonOptions, SettingsStore
from .evaluate import evaluate_day
from .forecast import HistoryBundle, build_day_forecast
from .hass import EntityState, HomeAssistant
from .prices import PriceProvider, load_csv
from .reconcile import actual_for_day
from .runner import Runner
from .store import Store
from .timeutil import UTC, slot_starts_utc, to_local

_LOG = logging.getLogger("dynprice.cli")


def _runner(args: argparse.Namespace) -> Runner:
    options = AddonOptions.from_env()
    if args.data_dir:
        options.data_dir = Path(args.data_dir)
    return Runner(
        options=options,
        settings_store=SettingsStore(options.data_dir / "settings.json"),
        store=Store(options.data_dir / "dynprice.db"),
        hass=HomeAssistant(),
        prices=PriceProvider(options.data_dir / "cache", offline=getattr(args, "offline", False)),
    )


def _print_evaluation(payload: dict) -> None:
    window = payload["window"]
    storage = payload["storage"]
    savings = payload["savings"]
    print(f"Tag                 {payload['day']}  (Szenario {payload['scenario']})")
    print(f"Netzbezug erwartet  {payload['energy']['import_kwh']:.1f} kWh, "
          f"PV {payload['energy']['pv_kwh']:.1f} kWh")
    print(f"Ladefenster         {window['start_utc']} bis {window['end_utc']}")
    print(f"Preis im Fenster    {window['avg_price_ct']:.2f} ct/kWh "
          f"(Tagesmittel ohne Verschiebung {payload['prices']['day_avg_ct']:.2f} ct/kWh)")
    print(f"Speicher            {storage['verdict']}: {storage['shifted_kwh']:.1f} von "
          f"{storage['needed_kwh']:.1f} kWh verschiebbar")
    for reason in storage["reasons"]:
        print(f"                    - {reason}")
    print(f"Kosten              dynamisch {payload['costs']['dynamic_shifted_eur']:.2f} EUR, "
          f"fix {payload['costs']['fixed_eur']:.2f} EUR")
    print(f"Ersparnis           {savings['vs_fixed_eur']:.2f} EUR "
          f"(ohne Verschiebung {savings['vs_fixed_unshifted_eur']:.2f}, "
          f"ohne Speichergrenzen {savings['vs_fixed_ideal_eur']:.2f})")
    for warning in payload["warnings"]:
        print(f"Warnung             {warning}")


def cmd_evaluate(args: argparse.Namespace) -> int:
    runner = _runner(args)
    day = date.fromisoformat(args.day) if args.day else (
        datetime.now(tz=runner.tz).date() + timedelta(days=1)
    )
    result = runner.run_day(day, publish_states=not args.no_publish)
    if result["status"] != "ok":
        print(f"Kein Ergebnis: {result.get('message', result['status'])}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result["evaluation"], ensure_ascii=False, indent=2))
    else:
        _print_evaluation(result["evaluation"])
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    runner = _runner(args)
    day = date.fromisoformat(args.day)
    settings = runner.settings_store.load()
    payload = actual_for_day(day, runner.tz, settings, runner.hass, runner.prices, runner.store)
    if payload.get("status") != "ok":
        print(f"Kein Ist-Wert: {payload.get('message')}", file=sys.stderr)
        return 1
    runner.store.save_actual(day, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else
          f"{day}: Bezug {payload['import_kwh']:.1f} kWh, dynamisch {payload['cost_dynamic_eur']:.2f} EUR, "
          f"fix {payload['cost_fixed_eur']:.2f} EUR, Ersparnis {payload['saving_eur']:.2f} EUR")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    runner = _runner(args)
    forecasts = runner.store.list_forecasts()
    actuals = runner.store.list_actuals()
    summaries = summarise(forecasts, actuals, args.granularity)
    if args.json:
        print(json.dumps([s.as_dict() for s in summaries], ensure_ascii=False, indent=2))
        return 0
    header = f"{'Zeitraum':10s} {'Tage':>5s} {'Prognose':>10s} {'Ist':>10s} {'Kap.-Lim':>9s} {'Leist.-Lim':>11s}"
    print(header)
    print("-" * len(header))
    for summary in summaries:
        print(f"{summary.label:10s} {summary.days:5d} {summary.forecast_saving_eur:10.2f} "
              f"{summary.realised_saving_eur:10.2f} {summary.share_capacity_limited:8.0%} "
              f"{summary.share_power_limited:10.0%}")
    print()
    for line in conclusion(summaries):
        print(line)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import serve

    runner = _runner(args)
    server = serve(runner, None, args.port)
    print(f"Oberfläche auf http://127.0.0.1:{args.port}/ (Abbruch mit Strg+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def cmd_import_prices(args: argparse.Namespace) -> int:
    """Preise aus einer CSV in den Cache legen, damit offline gerechnet werden kann."""
    runner = _runner(args)
    series = load_csv(Path(args.csv))
    days = sorted({to_local(point.start_utc, runner.tz).date() for point in series})
    written = 0
    for day in days:
        start = (day - timedelta(days=1))
        end = (day + timedelta(days=1))
        points = [p for p in series if start <= to_local(p.start_utc, runner.tz).date() <= end]
        payload = {
            "unix_seconds": [int(p.start_utc.timestamp()) for p in points],
            "price": [p.spot_eur_mwh for p in points],
            "unit": "EUR/MWh",
        }
        runner.prices._write_cache(start, end, payload)  # bewusst der gleiche Cache-Pfad
        written += 1
    print(f"{written} Tage in den Preis-Cache geschrieben: {days[0]} bis {days[-1]}")
    return 0


def _demo_prices(day: date, tz, rng: random.Random) -> list[float]:
    """Plausible Tageskurve: Nachttal, Mittagsdelle im Sommer, Abendspitze."""
    level = 70.0 + rng.uniform(-25.0, 45.0)
    values: list[float] = []
    for slot in slot_starts_utc(day, tz):
        hour = to_local(slot, tz).hour + to_local(slot, tz).minute / 60.0
        shape = (
            -35.0 * math.cos((hour - 4.0) / 24.0 * 2 * math.pi)
            - 30.0 * math.exp(-((hour - 13.0) ** 2) / 6.0)
            + 60.0 * math.exp(-((hour - 19.0) ** 2) / 4.0)
        )
        values.append(round(max(-40.0, level + shape + rng.uniform(-6.0, 6.0)), 2))
    return values


def cmd_demo(args: argparse.Namespace) -> int:
    """Beispieldaten erzeugen, um die Oberfläche ohne Home Assistant zu prüfen."""
    runner = _runner(args)
    rng = random.Random(args.seed)
    settings = runner.settings_store.load()
    settings.configured = True
    if settings.ev.kwh_per_night <= 0:
        settings.ev.kwh_per_night = 18.0
    runner.settings_store.save(settings)

    end = date.fromisoformat(args.end) if args.end else datetime.now(tz=runner.tz).date()
    start = end - timedelta(days=args.days - 1)
    day = start
    while day <= end:
        slots = slot_starts_utc(day, runner.tz)
        spot = _demo_prices(day, runner.tz, rng)
        runner.prices._write_cache(
            day - timedelta(days=1), day + timedelta(days=1),
            {
                "unix_seconds": [int(s.timestamp()) for s in slots],
                "price": spot,
                "unit": "EUR/MWh",
            },
        )
        history = []
        base_moment = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=28)
        for offset in range(28):
            for hour in range(24):
                load = 0.18 + (0.55 if 17 <= hour < 21 else 0.0) + (0.15 if 6 <= hour < 9 else 0.0)
                history.append((base_moment + timedelta(days=offset, hours=hour),
                                round(load * rng.uniform(0.85, 1.15), 3)))
        pv_kwh = max(0.0, 22.0 * math.cos((day.timetuple().tm_yday - 172) / 365.0 * 2 * math.pi) + 8.0)
        bundle = HistoryBundle(
            household_hourly=history,
            pv_forecast_state=EntityState(
                "sensor.demo_pv", f"{pv_kwh:.1f}", {"unit_of_measurement": "kWh"}
            ),
            battery_soc_pct=rng.uniform(15.0, 85.0),
            basis="netzbezug",
            notes=["Demodaten"],
        )
        forecast = build_day_forecast(day, slots, runner.tz, settings, bundle)
        evaluation = evaluate_day(
            forecast, spot, settings, runner.tz, settings.scenario_battery(),
            bundle.battery_soc_pct,
            generated_at=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        )
        runner.store.save_forecast(evaluation)

        if day < end:  # für vergangene Tage einen Ist-Wert mit Streuung erzeugen
            actual_import = evaluation.total_import_kwh * rng.uniform(0.85, 1.2)
            cost_dynamic = evaluation.cost_dynamic_shifted_eur * rng.uniform(0.9, 1.15)
            cost_fixed = actual_import * settings.tariff.fixed_price_ct / 100.0
            runner.store.save_actual(day, {
                "day": day.isoformat(),
                "status": "ok",
                "import_kwh": round(actual_import, 3),
                "cost_dynamic_eur": round(cost_dynamic, 4),
                "cost_fixed_eur": round(cost_fixed, 4),
                "saving_eur": round(cost_fixed - cost_dynamic - evaluation.base_price_delta_eur, 4),
                "note": "Demodaten",
            })
        day += timedelta(days=1)
    print(f"{args.days} Demotage erzeugt: {start} bis {end} in {runner.store.path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dynprice", description=__doc__)
    parser.add_argument("--data-dir", help="Ablageort für Datenbank, Einstellungen und Preis-Cache")
    parser.add_argument("--log-level", default="warning")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("evaluate", help="Einen Tag bewerten (Standard: morgen)")
    run.add_argument("--day")
    run.add_argument("--offline", action="store_true", help="nur zwischengespeicherte Preise nutzen")
    run.add_argument("--json", action="store_true")
    run.add_argument("--no-publish", action="store_true", help="keine Sensoren in HA schreiben")
    run.set_defaults(func=cmd_evaluate)

    rec = sub.add_parser("reconcile", help="Ist-Werte eines Tages nachtragen")
    rec.add_argument("--day", required=True)
    rec.add_argument("--offline", action="store_true")
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_reconcile)

    rep = sub.add_parser("report", help="Monats- oder Jahresauswertung")
    rep.add_argument("--granularity", choices=["month", "year"], default="month")
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(func=cmd_report)

    srv = sub.add_parser("serve", help="Oberfläche lokal starten")
    srv.add_argument("--port", type=int, default=8099)
    srv.add_argument("--offline", action="store_true")
    srv.set_defaults(func=cmd_serve)

    imp = sub.add_parser("import-prices", help="Preise aus CSV in den Cache legen")
    imp.add_argument("csv")
    imp.set_defaults(func=cmd_import_prices)

    demo = sub.add_parser("demo", help="Beispieldaten erzeugen")
    demo.add_argument("--days", type=int, default=60)
    demo.add_argument("--end")
    demo.add_argument("--seed", type=int, default=7)
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(levelname)s %(name)s %(message)s",
    )
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
