# naturstrom smart Vorschau

Bewertet täglich vorausschauend, ob sich der dynamische Tarif **naturstrom smart** am
Folgetag gegenüber dem Fixtarif lohnt, und prüft, ob der Batteriespeicher die dafür
nötige Verschiebung des Netzbezugs überhaupt tragen kann.

## Einrichtung

Nach dem Start das Ingress-Panel öffnen (Seitenleiste oder **Add-on → Weboberfläche
öffnen**) und den Reiter **Einrichtung** ausfüllen.

### Entitäten

Das Add-on liest die Zustandsliste aus Home Assistant und schlägt je Rolle passende
Sensoren vor. Pflicht sind drei Angaben:

| Rolle | erwartet | Beispiel |
|---|---|---|
| Netzbezug (Zähler) | kWh, `state_class: total_increasing` | IR-Lesekopf am Zähler |
| PV-Prognose Folgetag | kWh | Forecast.Solar „Geschätzte Energieproduktion morgen“ |
| Speicher-Ladezustand | % | Wechselrichter-SoC |

Optional, aber hilfreich:

| Rolle | wofür |
|---|---|
| Hausverbrauch | genaueres Lastprofil als der Zähler allein |
| Netzeinspeisung, PV-Erzeugung | Einordnung des PV-Überschusses, Ist-Abgleich |
| Wallbox (Ladeenergie) | trennt das Auto vom Haushaltsprofil und leitet den Ladebedarf ab |
| PV-Prognose heute | Fortschreibung des Ladezustands bis Mitternacht |
| Wärmepumpe | nur für das WP-Szenario |

Ist Home Assistant nicht erreichbar, lassen sich die Entity-IDs von Hand eintragen.

### Anlage

Vorbelegt sind 30 kWh Speicher und 11 kW Wallbox. Anzupassen sind mindestens
SoC-Grenzen, Lade- und Entladeleistung sowie der Wirkungsgrad. Die nutzbare Kapazität
ergibt sich aus Nennkapazität und SoC-Grenzen, kann aber überschrieben werden.

Der Ladebedarf des Autos wird aus der Wallbox-Historie abgeleitet, sobald „Ladebedarf je
Nacht“ auf 0 steht.

### Szenarien

Standardmäßig aus:

- **Speicher 60 kWh** – rechnet denselben Tag mit verdoppelter Kapazität, unveränderter Leistung.
- **Wärmepumpe** – zusätzlicher Netzbezug aus Jahresmenge und Monatsprofil.

## Was täglich passiert

Der Lauf startet um 13:30 Uhr, sobald die Day-Ahead-Preise für den Folgetag vorliegen.
Fehlen sie noch, wird alle 20 Minuten erneut versucht, längstens bis 23 Uhr. Ablauf:

1. **Preise** viertelstündlich für DE-LU von `api.energy-charts.info`; die Rohantwort
   landet im Cache unter `/data/cache`.
2. **Verbrauchsprognose** aus der stündlichen Langzeitstatistik des Zählers (28 Tage,
   getrennt nach Werktag und Wochenende), gemischt mit der hinterlegten Monatsreferenz.
   Auto und Wärmepumpe werden vorher herausgerechnet und danach als eigene Blöcke wieder
   aufgesetzt.
3. **PV-Prognose** aus der Forecast.Solar-Entität. Liefert sie eine zeitaufgelöste
   Prognose als Attribut, wird deren Form übernommen, sonst wird die Tagesmenge über eine
   berechnete Tagesform verteilt.
4. **Ladezustand** wird vom aktuellen Wert bis Mitternacht fortgeschrieben – zwischen
   Lauf und Tagesbeginn lädt die PV noch und der Abendverbrauch entlädt wieder.
5. **Basislauf** ohne Netzladung: PV deckt zuerst den Verbrauch, der Überschuss lädt den
   Speicher, der Rest wird eingespeist; verbleibender Verbrauch kommt aus dem Speicher
   und erst dann aus dem Netz. Ergebnis ist der erwartete Netzbezug je Viertelstunde.
6. **Ladefenster**: geprüft wird jedes zusammenhängende Fenster bis 10 Stunden Länge.
   Je Fenster wird die günstigste Ladung gegen den teuersten Netzbezug **danach**
   aufgerechnet, begrenzt durch freie Kapazität, Ladeleistung im Fenster und
   Entladeleistung im Bezugsslot. Gewählt wird das Fenster mit der größten Ersparnis.
7. **Speicherprüfung**, **Ablage** in `/data/naturstrom.db`, **Entitäten** in Home
   Assistant, **Nachtrag** der Ist-Werte vergangener Tage.

### Tarifformel

```
All-in-Arbeitspreis [ct/kWh] = 15,29 + 1,19 + Börsenpreis netto [ct/kWh] × 1,19
```

Dazu 15,74 €/Monat Grundpreis, tageweise anteilig in die Ersparnis eingerechnet.
Fixtarif: 31 ct/kWh. Alle Werte sind in der Einrichtung änderbar.

### Speicherprüfung

Als **Bedarf** zählt der Netzbezug nach dem Ladefenster, der teurer ist als Laden plus
Speicherverluste. Das Ergebnis ist eines von vier:

| Verdikt | Bedeutung |
|---|---|
| ausreichend | der gesamte lohnende Bedarf lässt sich verschieben |
| limitiert | nur ein Teil; die Gründe werden mit kWh benannt |
| nicht ausreichend | nichts davon lässt sich verschieben |
| keine Verschiebung nötig | der Preisunterschied deckt die Speicherverluste nicht |

Als Gründe kommen infrage: **Kapazität** (freie Kapazität zu Fensterbeginn nach Abzug des
Ladezustands und der für PV freigehaltenen Menge), **Ladeleistung** (kW × Fensterlänge),
**Entladeleistung** (Bezug oberhalb der maximalen Entladeleistung, etwa 11 kW Wallbox
gegen 10 kW Speicher) und **PV-Vorrang**.

## Entitäten in Home Assistant

| Entität | Einheit | Inhalt |
|---|---|---|
| `sensor.naturstrom_smart_ersparnis_folgetag` | EUR | Ersparnis gegenüber Fixtarif, inkl. Grundpreisanteil |
| `sensor.naturstrom_smart_ladefenster_start` | Zeitstempel | Beginn des empfohlenen Fensters |
| `sensor.naturstrom_smart_ladefenster_ende` | Zeitstempel | Ende des Fensters |
| `sensor.naturstrom_smart_ladefenster_preis` | ct/kWh | mengengewichteter Preis im Fenster |
| `sensor.naturstrom_smart_tagespreis` | ct/kWh | Tagesmittel ohne Verschiebung |
| `sensor.naturstrom_smart_netzbezug_prognose` | kWh | erwarteter Netzbezug |
| `sensor.naturstrom_smart_verschiebbare_energie` | kWh | tatsächlich verschiebbare Menge |
| `sensor.naturstrom_smart_speicherstatus` | – | ausreichend / limitiert / nicht ausreichend |
| `binary_sensor.naturstrom_smart_guenstiger_als_fixtarif` | on/off | Vergleich mit dem Fixtarif |

Die Attribute enthalten die Details: Gründe der Begrenzung, freie und benötigte Kapazität,
Ersparnis ohne Verschiebung und ohne Speichergrenzen.

Beispiel für eine Automation:

```yaml
automation:
  - alias: Speicher im günstigen Fenster aus dem Netz laden
    trigger:
      - platform: template
        value_template: >-
          {{ now() >= states('sensor.naturstrom_smart_ladefenster_start') | as_datetime
             and now() < states('sensor.naturstrom_smart_ladefenster_ende') | as_datetime }}
    condition:
      - condition: state
        entity_id: sensor.naturstrom_smart_speicherstatus
        state: "limitiert"
    action: []   # hier den eigenen Speicher ansteuern
```

Die Zustände werden über die Kern-API geschrieben. Nach einem Neustart von Home Assistant
sind sie erst wieder da, wenn das Add-on erneut rechnet – es tut das automatisch beim
eigenen Start.

## Auswertung

Der Reiter **Verlauf** zeigt Ersparnis je Tag (Prognose gegen Ist), die kumulierte Summe,
Monats- und Jahreszeilen mit Limitquoten und ungenutzter Speicherkapazität sowie ein
Kurzfazit. Der Ist-Abgleich nutzt die tatsächlich bezogene Energie aus der
Langzeitstatistik und die tatsächlichen Preise des Tages.

## Optionen des Add-ons

| Option | Standard | Bedeutung |
|---|---|---|
| `log_level` | `info` | Ausführlichkeit des Protokolls |
| `run_hour`, `run_minute` | 13:30 | Startzeit des täglichen Laufs |
| `timezone` | `Europe/Berlin` | Zeitzone für Tagesgrenzen und Anzeige |
| `price_source` | `energy-charts` | Quelle der Day-Ahead-Preise |

Alles Weitere steht in der Oberfläche und liegt in `/data/settings.json`.

## Grenzen des Modells

- Es wird **nichts gesteuert**. Das Fenster ist eine Empfehlung; ob und wie der Speicher
  aus dem Netz lädt, entscheiden eigene Automationen.
- Geladen wird in **einem zusammenhängenden Fenster**, und verschoben wird nur Bezug, der
  zeitlich **nach** dem Fenster liegt. Ein Übertrag über Mitternacht hinaus wird nicht
  gerechnet.
- Die für PV freizuhaltende Kapazität wird bewusst **vorsichtig** angesetzt: reserviert
  wird der gesamte nach dem Fenster erwartete PV-Überschuss.
- Ohne Sensor „Hausverbrauch“ dient der Zähler als Basis. Der PV-Eigenverbrauch steckt
  dann bereits im Profil; die Rechnung bleibt konsistent, das Profil ist aber kein
  reines Hausprofil.
- Der Ladebedarf des Autos wird als **täglich wiederkehrendes Nachtmuster** angesetzt.
- Die Qualität der Prognose hängt an PV-Prognose und Verbrauchshistorie. Die ersten Tage
  stützen sich stark auf die hinterlegte Monatsreferenz.
- Vergangene Zeiträume werden nicht rückwirkend bewertet.
