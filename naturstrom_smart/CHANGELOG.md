# Änderungen

## 0.1.0

Erste Fassung.

- Tägliche Bewertung des Folgetags: günstigstes Ladefenster, Ø All-in-Preis mit und ohne
  Verschiebung, erwarteter Netzbezug, Ersparnis gegenüber dem Fixtarif mit und ohne
  Speichergrenzen, Warnung wenn smart teurer wäre.
- Speicherprüfung mit Verdikt und benannten Gründen (Kapazität, Lade-, Entladeleistung,
  PV-Vorrang).
- Day-Ahead-Preise DE-LU von api.energy-charts.info mit lokalem Cache, dadurch offline
  wiederholbar.
- Verbrauchsprognose aus der Langzeitstatistik, gemischt mit einer Monatsreferenz;
  PV-Prognose aus der Forecast.Solar-Entität.
- Ablage jeder Prognose, Nachtrag der Ist-Werte, Monats- und Jahresauswertung.
- Ingress-Oberfläche mit Einrichtungsassistent, Diagrammen und Verlauf.
- Ergebnisse als Entitäten in Home Assistant.
- Optionale Szenarien: Speicher 60 kWh, Wärmepumpe.
- Wirkungsgrad ab Werk auf 0,85 (Victron-ESS mit LFP) und aus den Speicherzählern
  schätzbar, mit getrenntem Ausweis von gemessenem Speicher- und angenommenem
  Wandlerwirkungsgrad.
