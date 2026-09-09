# Änderungen

## 0.1.0

Erste Fassung.

Das Add-on ist an keinen Anbieter gebunden: Der Tarif wird über seine Bestandteile
beschrieben (fester Anteil je kWh, Servicepauschale, Faktor auf den Börsenpreis,
Grundpreis), die Vorbelegung entspricht einem dynamischen Tarif nach Tarifblatt
Stand September 2026.

- Tägliche Bewertung des Folgetags: günstigstes Ladefenster, Ø All-in-Preis mit und ohne
  Verschiebung, erwarteter Netzbezug, Ersparnis gegenüber dem Fixtarif mit und ohne
  Speichergrenzen, Warnung wenn der dynamische Tarif teurer wäre.
- Speicherprüfung mit Verdikt und benannten Gründen (Kapazität, Lade-, Entladeleistung,
  PV-Vorrang).
- Day-Ahead-Preise DE-LU von api.energy-charts.info mit lokalem Cache, dadurch offline
  wiederholbar.
- Verbrauchsprognose aus der Langzeitstatistik, gemischt mit einer Monatsreferenz;
  PV-Prognose aus der Forecast.Solar-Entität.
- Ablage jeder Prognose, Nachtrag der Ist-Werte, Monats- und Jahresauswertung.
- Ingress-Oberfläche mit Einrichtungsassistent, Diagrammen und Verlauf.
- Ergebnisse als Entitäten in Home Assistant, über MQTT-Discovery (dauerhaft, im
  Geräteregister) mit Rückfall auf die Zustands-API, wenn kein Broker da ist.
- Neustartfest: Dauerverbindung zum Broker, damit der Letzte Wille greift; erneutes
  Senden beim Start und wenn Home Assistant sich zurückmeldet; ausdrückliches
  Abmelden beim Beenden.
- Optionale Szenarien: Speicher 60 kWh, Wärmepumpe.
- Wirkungsgrad in drei Stufen (Zellen, Ladegerät, Wechselrichter) statt einer Zahl,
  daraus getrennte Wege für Netz- und PV-Ladung; bei DC-gekoppelter PV entfällt der
  Ladegerät-Verlust.
- PV-Anbindung einstellbar, vorbelegt mit AC-Kopplung: PV und Netz teilen sich dann
  Ladegeräte und Wirkungsgrad.
- Netzladeleistung aus dem Typenschild (Anzahl Geräte × Ladestrom × Batteriespannung),
  vorbelegt mit 3 × MultiPlus-II 48/5000/70 dreiphasig.
- PV-Reserve ab Fensterbeginn statt ab Fensterende gerechnet; sonst konnte der
  Optimierer das Fenster über den PV-Tag hinaus verlängern und die Reserve wegrechnen.
- Netzladeleistung getrennt von der PV-Ladeleistung: Netzladung läuft über das
  Ladegerät des Wechselrichters und ist meist deutlich kleiner als das, was der
  Speicher aus den MPPT-Reglern annimmt.
- Lade- und Entladegrenzen sowie Mindest-SoC und Eingangsstrombegrenzung wahlweise
  live aus Home Assistant, mit Umrechnung von Ampere in Leistung.
- Wirkungsgrad ab Werk auf 0,85 (Victron-ESS mit LFP) und aus den Speicherzählern
  schätzbar, mit getrenntem Ausweis von gemessenem Speicher- und angenommenem
  Wandlerwirkungsgrad.
