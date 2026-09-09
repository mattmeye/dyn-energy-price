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

from .evaluate import DayEvaluation
from .hass import supervisor_service
from .publish import DEVICE_NAME, PREFIX, SensorSpec, build_sensors

_LOG = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"
STATUS_TOPIC = f"{PREFIX}/status"
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


def messages(evaluation: DayEvaluation) -> list[tuple[str, str, bool]]:
    """Alle zu sendenden Nachrichten als Topic, Nutzlast, retain."""
    out: list[tuple[str, str, bool]] = []
    for sensor in build_sensors(evaluation):
        t = topics(sensor)
        out.append((t["config"], json.dumps(discovery_payload(sensor), ensure_ascii=False), True))
        out.append((t["availability"], ONLINE if sensor.state is not None else OFFLINE, True))
        if sensor.state is not None:
            out.append((t["state"], sensor.state, True))
        out.append((t["attributes"], json.dumps(sensor.attributes, ensure_ascii=False), True))
    return out


class MqttPublisher:
    """Verbindet sich bei Bedarf und sendet die Entitäten dauerhaft (retained)."""

    def __init__(self, broker: BrokerInfo, client_id: str = PREFIX) -> None:
        self.broker = broker
        self.client_id = client_id

    def _client(self):
        import paho.mqtt.client as mqtt  # lazy, damit Tests ohne Broker auskommen

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=self.client_id)
        if self.broker.username:
            client.username_pw_set(self.broker.username, self.broker.password)
        if self.broker.ssl:
            client.tls_set()
        # Letzter Wille: fällt das Add-on aus, werden die Entitäten unverfügbar.
        client.will_set(STATUS_TOPIC, OFFLINE, qos=1, retain=True)
        return client

    def publish(self, evaluation: DayEvaluation, timeout: float = 15.0) -> int:
        payloads = messages(evaluation)
        client = self._client()
        client.connect(self.broker.host, self.broker.port, keepalive=60)
        client.loop_start()
        try:
            client.publish(STATUS_TOPIC, ONLINE, qos=1, retain=True)
            for topic, payload, retain in payloads:
                client.publish(topic, payload, qos=1, retain=retain)
            client.loop_stop()
            client.disconnect()
        except Exception:
            try:
                client.loop_stop()
            except Exception:
                pass
            raise
        return len(payloads)

    def clear(self, keys: Sequence[str], domains: Sequence[str]) -> None:
        """Discovery-Einträge entfernen, indem leere Nutzlasten gesendet werden."""
        client = self._client()
        client.connect(self.broker.host, self.broker.port, keepalive=30)
        client.loop_start()
        for domain, key in zip(domains, keys):
            client.publish(f"{DISCOVERY_PREFIX}/{domain}/{PREFIX}/{key}/config", "", qos=1, retain=True)
        client.loop_stop()
        client.disconnect()
