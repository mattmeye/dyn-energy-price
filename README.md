# naturstrom smart Vorschau

Home-Assistant-Add-on, das **jeden Tag vorausschauend für den Folgetag** ermittelt:

1. wie viel der dynamische Tarif **naturstrom smart** gegenüber dem Fixtarif (31 ct/kWh) einspart,
   wenn der Netzbezug in die günstigsten Stunden verschoben wird,
2. ob der Batteriespeicher dafür rechnerisch genug **Kapazität und Leistung** hat,
3. welche Energiemenge **nicht** verschiebbar ist und warum (Kapazität, Leistung, PV-Vorrang).

Der Blick geht ausschließlich nach vorn: bewertet wird ab Inbetriebnahme, es gibt keine
rückwirkende Auswertung vergangener Zeiträume. Jede Prognose wird abgelegt und später
mit dem tatsächlichen Verlauf verglichen.

Das Add-on **steuert nichts** – es rechnet und stellt das Ergebnis als Entitäten bereit,
die in eigenen Automationen genutzt werden können.

## Installation

1. In Home Assistant unter **Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories**
   dieses Repository hinzufügen: `https://github.com/mattmeye/dyn-energy-price`
2. Das Add-on **naturstrom smart Vorschau** installieren und starten.
3. Das Ingress-Panel öffnen und unter **Einrichtung** die Entitäten auswählen. Passende
   Sensoren werden automatisch erkannt und vorgeschlagen; die Speicherwerte sind mit
   30 kWh und 11 kW Wallbox vorbelegt.
4. Speichern. Der erste Lauf startet sofort, danach täglich um 13:30 Uhr.

Details zur Bedienung, zu den Rechenwegen und zu den Grenzen des Modells stehen in
[naturstrom_smart/DOCS.md](naturstrom_smart/DOCS.md).

## Aufbau des Repositories

```
repository.yaml               Add-on-Repository für Home Assistant
naturstrom_smart/             das Add-on
  config.yaml                 Add-on-Manifest (Ingress, Berechtigungen, Optionen)
  Dockerfile, build.yaml      Abbild auf Basis der HA-Python-Images
  run.sh                      Startskript (liest die Add-on-Optionen)
  DOCS.md                     Anwenderdokumentation
  nsforecast/                 Python-Paket
    config.py                 Einstellungen und Add-on-Optionen
    hass.py                   REST- und WebSocket-Zugriff auf Home Assistant
    discovery.py              automatische Erkennung passender Entitäten
    prices.py                 Day-Ahead-Preise von api.energy-charts.info samt Cache
    pvforecast.py             PV-Prognose aus der Forecast.Solar-Entität
    forecast.py               Verbrauchsprognose aus Historie und Monatsreferenz
    battery.py                Speichersimulation und Speicherprüfung
    evaluate.py               Ladefenster-Optimierung und Tagesbewertung
    store.py                  SQLite-Ablage für Prognose, Ist-Werte und Läufe
    reconcile.py              Nachtrag der tatsächlichen Werte
    aggregate.py              Monats- und Jahresauswertung
    publish.py                Entitäten in Home Assistant
    scheduler.py, runner.py   täglicher Ablauf
    web.py, web/static/       Ingress-Oberfläche
    cli.py                    Kommandozeile für Offline-Läufe
tests/                        pytest-Suite
```

## Lokal rechnen und entwickeln

Das Paket läuft mit der Standardbibliothek; `requests` und `websocket-client` werden nur
im Add-on gebraucht.

```bash
python3 -m pytest                                   # Testsuite

export PYTHONPATH=naturstrom_smart
python3 -m nsforecast.cli --data-dir ./daten demo --days 60      # Beispieldaten
python3 -m nsforecast.cli --data-dir ./daten serve --offline     # Oberfläche auf :8099
python3 -m nsforecast.cli --data-dir ./daten evaluate --day 2026-09-09 --offline
python3 -m nsforecast.cli --data-dir ./daten report
```

Ein Lauf legt die Antwort von energy-charts im Cache ab. Damit ist derselbe Tag später
**offline wiederholbar** (`--offline`); eigene Preisreihen lassen sich mit
`import-prices <datei.csv>` einspielen.

## Rahmen

- Zeitzone Europe/Berlin, Rohdaten und Ablage in UTC.
- Beträge in Euro, Auflösung Tag und Monat.
- Läuft neben Home Assistant im selben System, ohne Cloud-Dienst.
