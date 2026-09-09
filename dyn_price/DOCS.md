# Dynamischer Strompreis

Bewertet täglich vorausschauend, ob sich ein **dynamischer Stromtarif** am Folgetag
gegenüber dem Fixtarif lohnt, und prüft, ob der Batteriespeicher die dafür nötige
Verschiebung des Netzbezugs überhaupt tragen kann.

Der Tarif wird über seine Bestandteile beschrieben, nicht über einen Anbieternamen:
ein fester Anteil je kWh (Netzentgelte, Abgaben, Umlagen, Steuern), eine
Servicepauschale, ein Faktor auf den Börsenpreis und ein monatlicher Grundpreis.
Damit lässt sich jeder Tarif abbilden, dessen Arbeitspreis am Day-Ahead-Preis hängt.

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
| Speicher-Ladung und -Entladung | Messung des Wirkungsgrads aus den eigenen Zählern |
| Ladestromgrenze (CCL), Entladestromgrenze (DCL) | Grenzen live statt fest, siehe unten |
| Batteriespannung | rechnet Stromgrenzen in Ampere in Leistung um |
| Mindest-Ladezustand, Eingangsstrombegrenzung | ESS-Einstellungen live übernehmen |
| PV-Prognose heute | Fortschreibung des Ladezustands bis Mitternacht |
| Wärmepumpe | nur für das WP-Szenario |

Ist Home Assistant nicht erreichbar, lassen sich die Entity-IDs von Hand eintragen.

### Anlage

Vorbelegt sind 30 kWh Speicher und 11 kW Wallbox. Anzupassen sind mindestens
SoC-Grenzen, Lade- und Entladeleistung sowie der Wirkungsgrad. Die nutzbare Kapazität
ergibt sich aus Nennkapazität und SoC-Grenzen, kann aber überschrieben werden.

#### Lade- und Entladeleistung

Für die Bewertung sind es **drei verschiedene Wege**, nicht einer:

| Weg | worüber | Vorbelegung |
|---|---|---|
| PV in den Speicher | AC-gekoppelt über dieselben Ladegeräte | **10,75 kW**, geteilt mit dem Netz |
| Netz in den Speicher | Ladegeräte der Wechselrichter | 3 × 70 A × 51,2 V = **10,75 kW** |
| Speicher ins Haus | Wechselrichter | 3 × 4 kW = **12 kW** |

Die Vorbelegung entspricht der Anlage: **3 × MultiPlus-II 48/5000/70, dreiphasig,
AC-gekoppelte PV**. Weil die PV über einen eigenen Wechselrichter einspeist, laden
PV-Überschuss und Netz über dieselben Ladegeräte: Was die PV gerade einspeichert,
steht im selben Moment nicht fürs Netzladen zur Verfügung, und beide Wege haben
denselben Wirkungsgrad. Bei DC-Kopplung über MPPT-Regler wäre der PV-Weg getrennt,
leistungsfähiger und verlustärmer – umstellbar in der Einrichtung.
Der Ladestrom steht auf dem Typenschild hinter dem zweiten Schrägstrich; Anzahl und
Strom werden eingetragen, die Netzladeleistung rechnet das Add-on daraus mit der
Batteriespannung aus. Bei abweichenden Geräten also nur diese beiden Zahlen ändern.

Für die Verschiebung zählt die **mittlere** Zeile – sie bestimmt, wie lang das
Ladefenster ausfallen muss:

| Netzladeleistung | Fenster | verschoben | Ersparnis |
|---|---|---|---|
| 3,58 kW (ein Gerät) | 4,00 h | 12,2 kWh | 1,31 € |
| 10,75 kW (drei Geräte) | 2,75 h | 24,8 kWh | 1,77 € |

(Wintertag, 30 kWh Bezug, günstiges Fenster 01–05 Uhr)

Zusätzlich deckelt die **Eingangsstrombegrenzung** der Wechselrichter, was aus dem
Netz durch die Geräte fließt: 16 A dreiphasig sind 11,0 kW, 32 A sind 22,1 kW. Bei
drei Geräten liegt die Grenze also erst bei 16 A und darunter im Weg. Der Wert steht
in der Victron-Oberfläche unter Einstellungen → System → AC-Eingangsstrombegrenzung.

#### Grenzen live aus Home Assistant

Victron gibt die Grenzen als **Strom** aus: DVCC meldet eine Ladestromgrenze (CCL)
und eine Entladestromgrenze (DCL) in Ampere, und das BMS senkt beide bei kalten
Zellen oder hohem Ladezustand ab. Wird eine Entität ausgewählt, übernimmt das
Add-on deren Wert und rechnet Ampere mit der Batteriespannung in Leistung um –
aus der Spannungs-Entität, sonst mit dem hinterlegten Nennwert.

Der Lauf findet nachmittags statt, gerechnet wird die Nacht. Liegt der kleinste
Wert der letzten sieben Tage deutlich unter dem aktuellen, vermerkt das Add-on
das im Ergebnis: dann greift die Absenkung durch das BMS regelmäßig, und der
Momentanwert ist zu optimistisch. Gerechnet wird trotzdem mit dem aktuellen Wert.

Dasselbe gilt für den **Mindest-Ladezustand**: Ist die ESS-Einstellung als Entität
hinterlegt, wird sie live gelesen statt fest eingetragen.

#### Wirkungsgrad

Statt einer Zahl stehen drei Stufen in der Einrichtung, weil nur die erste messbar ist:

| Stufe | Vorbelegung | Herkunft |
|---|---|---|
| Zellen DC→DC | 0,97 | aus den eigenen Zählern messbar |
| Ladegerät AC→DC | 0,93 | Geräteeigenschaft |
| Wechselrichter DC→AC | 0,94 | Geräteeigenschaft |

Daraus ergeben sich zwei Wege, die das Add-on getrennt rechnet:

- **Netz → Speicher → Haus: 0,848.** Maßgeblich für die Verschiebung, weil nur
  dieser Weg beim Verlagern des Netzbezugs durchlaufen wird.
- **PV → Speicher → Haus: 0,848** bei der AC-gekoppelten Anlage, weil auch die PV
  durch die Ladegeräte muss. Bei DC-Kopplung wären es 0,912.

Gegenüber einer DC-gekoppelten Anlage kostet das in der Übergangszeit 0,1 bis
1,3 kWh Netzbezug am Tag – im Winter nichts, weil kaum PV in den Speicher geht,
im Hochsommer nichts, weil der Speicher ohnehin voll wird.

Der Standby der Geräte gehört in **keine** dieser Stufen. Er läuft unabhängig vom
Laden und steckt bereits im gemessenen Verbrauchsprofil; hier wäre er doppelt gezählt.
Bei drei MultiPlus-II sind das grob 45–60 W dauerhaft, also rund 1,2 kWh am Tag.

Die Schaltfläche **Zellen-Wirkungsgrad aus Speicherdaten schätzen** wertet die Zähler
für Speicher-Ladung und -Entladung der letzten 30 Tage aus. Bei Victron messen die am
Shunt, also gleichstromseitig – genau die erste Stufe. Liegt das Verhältnis außerhalb
von 0,80 bis 1,00, passen die beiden Zähler nicht zusammen; dann bleibt der typische
LFP-Wert stehen und das Ergebnis vermerkt es. Der Vorschlag landet im Feld,
gespeichert wird er erst mit **Speichern**.

Der Wert wirkt doppelt: Er verringert die verschiebbare Menge, und er hebt die
Schwelle, ab der sich eine Verschiebung lohnt. Bei einem Ladepreis von 19,5 ct/kWh
muss der verdrängte Bezug bei 0,90 mindestens 21,6 ct kosten, bei 0,85 schon 22,9 ct.

### Szenarien

Standardmäßig aus:

- **Speicher 60 kWh** – rechnet denselben Tag mit verdoppelter Kapazität, unveränderter
  Leistung. Wie viel das bringt, hängt an der Netzladeleistung: mit 10,75 kW sind es im
  Testtag +0,11 €, mit einem einzelnen Gerät nichts, weil in den günstigen Stunden
  ohnehin nicht mehr nachladbar ist.
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
7. **Speicherprüfung**, **Ablage** in `/data/dynprice.db`, **Entitäten** in Home
   Assistant, **Nachtrag** der Ist-Werte vergangener Tage.

### Tarifformel

```
All-in-Arbeitspreis [ct/kWh] = fester Anteil + Servicepauschale
                             + Börsenpreis netto [ct/kWh] × Faktor
```

| Bestandteil | Vorbelegung | Bedeutung |
|---|---|---|
| fester Anteil | 15,29 ct | Netzentgelte, Abgaben, Umlagen, Steuern |
| Servicepauschale | 1,19 ct | Aufschlag des Anbieters je kWh |
| Faktor auf den Börsenpreis | 1,19 | Umsatzsteuer auf den Day-Ahead-Preis |
| Grundpreis | 15,74 €/Monat | tageweise anteilig gegen den Fixtarif gerechnet |
| Fixtarif | 31 ct/kWh | Vergleichsmaßstab, Grundpreis getrennt einstellbar |

Die Vorbelegung entspricht einem dynamischen Tarif nach Tarifblatt Stand September 2026.
Alle Werte stehen in der Einrichtung und lassen sich auf jeden anderen Tarif umstellen,
dessen Arbeitspreis am Day-Ahead-Preis hängt.

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
Ladezustands und der für PV freigehaltenen Menge), **Netzladeleistung** (kW des Ladegeräts × Fensterlänge),
**Entladeleistung** (Bezug oberhalb der maximalen Entladeleistung, etwa 11 kW Wallbox
gegen 10 kW Speicher) und **PV-Vorrang**.

## Entitäten in Home Assistant

| Entität | Einheit | Inhalt |
|---|---|---|
| `sensor.dyn_price_ersparnis_folgetag` | EUR | Ersparnis gegenüber Fixtarif, inkl. Grundpreisanteil |
| `sensor.dyn_price_ladefenster_start` | Zeitstempel | Beginn des empfohlenen Fensters |
| `sensor.dyn_price_ladefenster_ende` | Zeitstempel | Ende des Fensters |
| `sensor.dyn_price_ladefenster_preis` | ct/kWh | mengengewichteter Preis im Fenster |
| `sensor.dyn_price_tagespreis` | ct/kWh | Tagesmittel ohne Verschiebung |
| `sensor.dyn_price_netzbezug_prognose` | kWh | erwarteter Netzbezug |
| `sensor.dyn_price_verschiebbare_energie` | kWh | tatsächlich verschiebbare Menge |
| `sensor.dyn_price_speicherstatus` | – | ausreichend / limitiert / nicht ausreichend |
| `binary_sensor.dyn_price_guenstiger_als_fixtarif` | on/off | Vergleich mit dem Fixtarif |

Die Attribute enthalten die Details: Gründe der Begrenzung, freie und benötigte Kapazität,
Ersparnis ohne Verschiebung und ohne Speichergrenzen.

### Zwei Wege, ein Ergebnis

| Weg | Voraussetzung | Verhalten |
|---|---|---|
| **MQTT-Discovery** | ein Broker, üblicherweise das Add-on Mosquitto | Echte Entitäten im Geräteregister: überstehen einen Neustart von Home Assistant, lassen sich umbenennen, in Dashboards ziehen und in Automationen auswählen. Alle Nachrichten sind retained, das Gerät heißt *Dynamischer Strompreis*. |
| **Zustands-API** | nichts weiter | Notnagel ohne Broker. Die Entitäten sind nach einem Neustart von Home Assistant weg, bis das Add-on erneut rechnet – es tut das beim eigenen Start. |

Voreingestellt ist **Automatisch**: Ist ein Broker vorhanden, nimmt das Add-on MQTT,
sonst die Zustands-API. Der Weg steht in der Einrichtung unter *Szenarien* und im
Laufprotokoll (`Entitäten über MQTT` bzw. `über Zustands-API`).

Fehlt ein Wert – etwa der Zeitstempel des Ladefensters an einem Tag ohne lohnendes
Fenster –, wird die betroffene Entität über MQTT als *nicht verfügbar* gemeldet statt
mit einem Ersatzwert gefüllt.

Beispiel für eine Automation:

```yaml
automation:
  - alias: Speicher im günstigen Fenster aus dem Netz laden
    trigger:
      - platform: template
        value_template: >-
          {{ now() >= states('sensor.dyn_price_ladefenster_start') | as_datetime
             and now() < states('sensor.dyn_price_ladefenster_ende') | as_datetime }}
    condition:
      - condition: state
        entity_id: sensor.dyn_price_speicherstatus
        state: "limitiert"
    action: []   # hier den eigenen Speicher ansteuern
```



## Neustarts

| Was neu startet | Was passiert |
|---|---|
| **Home Assistant** | Die Entitäten kommen von selbst zurück: Discovery und Werte liegen als retained-Nachrichten im Broker. Zusätzlich meldet sich Home Assistant beim Hochfahren auf `homeassistant/status`; das Add-on sendet daraufhin alles erneut. |
| **Das Add-on** | Beim Start wird zuerst das zuletzt abgelegte Ergebnis erneut gesendet – ohne Netz und ohne Preise, die Entitäten haben also sofort wieder Werte. Danach läuft die normale Neuberechnung. |
| **Der Host** | Wie ein Neustart des Add-ons. `/data` liegt auf der Festplatte. |
| **Der MQTT-Broker** | Mosquitto hält retained-Nachrichten über einen Neustart. Das Add-on hält die Verbindung offen und verbindet sich automatisch neu. |
| **Add-on gestoppt oder abgestürzt** | Der Broker meldet die Entitäten über den Letzten Willen als *nicht verfügbar*. Sie zeigen dann nichts an, statt stumm veraltete Werte weiterzuführen. |

Die Verbindung zum Broker bleibt bestehen, solange das Add-on läuft – nur so greift
der Letzte Wille. Beim geplanten Beenden meldet sich das Add-on ausdrücklich ab.

Über die **Zustands-API** (ohne Broker) gilt das alles nicht: Dort sind die Entitäten
nach einem Neustart von Home Assistant weg, bis das Add-on erneut rechnet. Es tut das
beim eigenen Start, aber ein HA-Neustart allein löst nichts aus.

### Was in `/data` liegt und Neustarts übersteht

| Datei | Inhalt |
|---|---|
| `settings.json` | alle Einstellungen aus der Oberfläche |
| `dynprice.db` | Prognosen, Ist-Werte, Laufprotokoll |
| `cache/` | Rohantworten von energy-charts, macht Läufe offline wiederholbar |

Nicht dauerhaft ist nur der Merker, ob heute schon gerechnet wurde. Nach einem Neustart
rechnet das Add-on deshalb einmal zusätzlich – das Ergebnis überschreibt den Eintrag
desselben Tages und richtet keinen Schaden an.

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

Der MQTT-Dienst wird als `mqtt:want` angefordert: Ist ein Broker installiert, bekommt
das Add-on dessen Zugangsdaten vom Supervisor, ohne dass etwas einzutragen wäre.

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
