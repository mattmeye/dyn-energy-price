"""Entitäten über MQTT-Discovery bereitstellen.

Anders als die Zustands-API legt Discovery echte Entitäten im Geräteregister an:
Sie überstehen einen Neustart von Home Assistant, lassen sich umbenennen, in
Dashboards ziehen und in Automationen auswählen. Die Zugangsdaten zum Broker
liefert der Supervisor, sobald das Add-on den Dienst mqtt anfordert.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from .hass import supervisor_service
from .publish import DEVICE_NAME, PREFIX, SensorSpec, build_sensors

_LOG = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"
STATUS_TOPIC = f"{PREFIX}/status"
HA_STATUS_TOPIC = f"{DISCOVERY_PREFIX}/status"
ONLINE = "online"
OFFLINE = "offline"


@dataclass(frozen=True)
class BrokerInfo:
    host: str
    port: int
    username: str = ""
    password: str = ""
    ssl: bool = False


def broker_from_supervisor(token: str | None = None) -> BrokerInfo | None:
    """Zugangsdaten des vom Supervisor bereitgestellten Brokers."""
    data = supervisor_service("mqtt", token)
    if not data or not data.get("host"):
        return None
    return BrokerInfo(
        host=str(data["host"]),
        port=int(data.get("port", 1883)),
        username=str(data.get("username", "")),
        password=str(data.get("password", "")),
        ssl=bool(data.get("ssl", False)),
    )


def _device() -> dict[str, Any]:
    return {
        "identifiers": [PREFIX],
        "name": DEVICE_NAME,
        "manufacturer": "mattmeye",
        "model": "Dynamischer Strompreis",
    }


def topics(sensor: SensorSpec) -> dict[str, str]:
    base = f"{PREFIX}/{sensor.key}"
    return {
        "state": f"{base}/state",
        "attributes": f"{base}/attributes",
        "availability": f"{base}/availability",
        "config": f"{DISCOVERY_PREFIX}/{sensor.domain}/{PREFIX}/{sensor.key}/config",
    }


def discovery_payload(sensor: SensorSpec) -> dict[str, Any]:
    """Discovery-Konfiguration für eine Entität."""
    t = topics(sensor)
    payload: dict[str, Any] = {
        "name": sensor.name,
        "unique_id": sensor.unique_id,
        "object_id": sensor.unique_id,
        "state_topic": t["state"],
        "json_attributes_topic": t["attributes"],
        # Zwei Quellen: das Add-on insgesamt und der einzelne Wert. Fehlt eines
        # von beiden, ist die Entität nicht verfügbar statt falsch.
        "availability": [{"topic": STATUS_TOPIC}, {"topic": t["availability"]}],
        "availability_mode": "all",
        "device": _device(),
    }
    if sensor.unit:
        payload["unit_of_measurement"] = sensor.unit
    if sensor.device_class:
        payload["device_class"] = sensor.device_class
    if sensor.state_class:
        payload["state_class"] = sensor.state_class
    if sensor.icon:
        payload["icon"] = sensor.icon
    return payload


def messages(payload: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Alle zu sendenden Nachrichten als Topic, Nutzlast, retain."""
    out: list[tuple[str, str, bool]] = []
    for sensor in build_sensors(payload):
        t = topics(sensor)
        out.append((t["config"], json.dumps(discovery_payload(sensor), ensure_ascii=False), True))
        out.append((t["availability"], ONLINE if sensor.state is not None else OFFLINE, True))
        if sensor.state is not None:
            out.append((t["state"], sensor.state, True))
        out.append((t["attributes"], json.dumps(sensor.attributes, ensure_ascii=False), True))
    return out


class MqttSession:
    """Dauerverbindung zum Broker.

    Die Verbindung bleibt bestehen, solange das Add-on läuft. Nur so greift der
    Letzte Wille: Bricht das Add-on weg, meldet der Broker die Entitäten als
    nicht verfügbar. Eine Verbindung, die nach jedem Senden sauber getrennt
    wird, löst ihn gerade nicht aus - die Entitäten sähen dann für immer
    verfügbar aus und zeigten veraltete Werte.
    """

    def __init__(
        self,
        broker: BrokerInfo,
        client_id: str = PREFIX,
        on_home_assistant_online: Any = None,
    ) -> None:
        self.broker = broker
        self.client_id = client_id
        self.on_home_assistant_online = on_home_assistant_online
        self._client: Any = None

    # ------------------------------------------------------------- Aufbau

    def _build_client(self) -> Any:
        import paho.mqtt.client as mqtt  # lazy, damit Tests ohne Broker auskommen

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=self.client_id)
        if self.broker.username:
            client.username_pw_set(self.broker.username, self.broker.password)
        if self.broker.ssl:
            client.tls_set()
        client.will_set(STATUS_TOPIC, OFFLINE, qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        _LOG.info("Mit MQTT-Broker verbunden")
        client.publish(STATUS_TOPIC, ONLINE, qos=1, retain=True)
        # Startet Home Assistant neu, meldet es sich hier; dann wird alles neu gesendet.
        client.subscribe(HA_STATUS_TOPIC, qos=1)

    def _on_message(self, client, userdata, message) -> None:
        if message.topic != HA_STATUS_TOPIC:
            return
        if message.payload.decode("utf-8", "ignore").strip().lower() != ONLINE:
            return
        _LOG.info("Home Assistant ist wieder da, Entitäten werden erneut gesendet")
        if self.on_home_assistant_online:
            try:
                self.on_home_assistant_online()
            except Exception:
                _LOG.exception("Erneutes Senden nach HA-Neustart fehlgeschlagen")

    def start(self) -> None:
        if self._client is not None:
            return
        client = self._build_client()
        client.connect(self.broker.host, self.broker.port, keepalive=60)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        """Sauber abmelden: Entitäten werden ausdrücklich als offline gemeldet."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.publish(STATUS_TOPIC, OFFLINE, qos=1, retain=True).wait_for_publish(timeout=5)
        except Exception:
            pass
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------- Senden

    def publish(self, payload: dict[str, Any]) -> int:
        """Ein Tagesergebnis senden; alle Nachrichten bleiben im Broker liegen."""
        self.start()
        nachrichten = messages(payload)
        for topic, body, retain in nachrichten:
            self._client.publish(topic, body, qos=1, retain=retain)
        return len(nachrichten)

    def clear(self, keys: Sequence[str], domains: Sequence[str]) -> None:
        """Discovery-Einträge entfernen, indem leere Nutzlasten gesendet werden."""
        self.start()
        for domain, key in zip(domains, keys):
            self._client.publish(
                f"{DISCOVERY_PREFIX}/{domain}/{PREFIX}/{key}/config", "", qos=1, retain=True
            )
