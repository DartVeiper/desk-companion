"""Подписка на топики ПК-агента — контракт из п.5 плана.

Разбор сообщений отделён от транспорта: apply_message — чистая функция над
состоянием, поэтому весь протокол проверяется тестами без брокера. Клиент
подключается только на живом Pi.

Про heartbeat. П.5 называет его обязательным, и не зря: без него при обрыве
WiFi или зависании агента Pi молча продолжает показывать последние данные
как свежие. Здесь его возраст и решает, считается ли ПК живым — само
отсутствие сообщений ничего не сообщает, потому что молчание неотличимо
от «ничего не изменилось».
"""

from __future__ import annotations

import json
from datetime import datetime

from ..state import State
from .base import Source

# Топики ровно как в п.5 плана.
ACTIVE_APP = "home/pc/active_app"
ACTIVITY = "home/pc/activity_1min"
AUDIO = "home/pc/audio"
HEARTBEAT = "home/pc/heartbeat"
HARDWARE = "home/pc/hardware"
ANOMALY = "home/pc/anomaly"
MEDIA = "home/pc/media"

SUBSCRIPTIONS = (ACTIVE_APP, ACTIVITY, AUDIO, HEARTBEAT, HARDWARE, ANOMALY, MEDIA)

# Топики, которые публикует сам Pi. С retain — чтобы после перезапуска
# сервиса подписчик сразу получал последнее известное значение, а не пустоту.
PRESENCE_OUT = "home/desk/presence"
ENV_OUT = "home/desk/env"
WEATHER_OUT = "home/desk/weather"
MANUAL_OUT = "home/desk/manual_status"


def _truthy(payload: str) -> bool:
    return payload.strip().lower() in ("1", "true", "on", "yes")


def apply_message(state: State, topic: str, payload: str, now: datetime | None = None,
                  retained: bool = False) -> bool:
    """Применить сообщение к состоянию. True — что-то изменилось.

    Битый JSON от компьютера не должен ронять сервис: часы обязаны
    показывать время, даже когда на игровом ПК творится ерунда.

    retained — сообщение отдал брокер из сохранённых при подписке, а не
    прислал компьютер только что. Такое уже было учтено до перезапуска
    сервиса, и считать его заново нельзя.
    """
    now = now or state.now
    pc = state.pc

    try:
        if topic == HEARTBEAT:
            pc.last_heartbeat = now
            return True

        if topic == AUDIO:
            value = _truthy(payload)
            if value == pc.audio_active:
                return False
            pc.audio_active = value
            return True

        data = json.loads(payload)

        if topic == ACTIVE_APP:
            pc.active_app = str(data.get("app", ""))
            pc.category = str(data.get("category", ""))
            return True

        if topic == ACTIVITY:
            keys, clicks = int(data.get("keys", 0)), int(data.get("clicks", 0))
            if retained:
                pc.keystrokes, pc.mouse_clicks = keys, clicks
            else:
                pc.note_activity(keys, clicks)
            # Агент шлёт признак отошедшего, экран активности его показывает,
            # а разбор его молча терял — значок AFK не зажигался никогда.
            pc.afk = bool(data.get("afk", False))
            return True

        if topic == HARDWARE:
            for field in ("gpu_temp", "gpu_load", "cpu_temp", "cpu_load"):
                value = data.get(field)
                setattr(pc, field, None if value is None else float(value))
            return True

        if topic == MEDIA:
            artist = str(data.get("artist", ""))
            title = str(data.get("title", ""))
            playing = bool(data.get("playing", False))
            if (artist, title, playing) == (pc.track_artist, pc.track_title,
                                            pc.track_playing):
                return False
            pc.track_artist, pc.track_title, pc.track_playing = artist, title, playing
            return True

        if topic == ANOMALY:
            pc.anomaly_flag = bool(data.get("flag", False))
            pc.anomaly_reason = str(data.get("reason", ""))
            return True

    except (ValueError, TypeError, AttributeError):
        return False  # мусор в топике — молча игнорируем

    return False


class MqttSource(Source):
    """Слушает брокер и наполняет State.pc.

    Клиент paho работает своим потоком, поэтому poll() ничего не ждёт: он
    только сообщает циклу, приходило ли что-то с прошлого раза.
    """

    name = "mqtt"
    interval = 0.5

    def __init__(self, host: str = "localhost", port: int = 1883,
                 client_id: str = "desk-companion") -> None:
        super().__init__()
        self.host, self.port, self.client_id = host, port, client_id
        self._client = None
        self._pending: list[tuple[str, str, bool]] = []
        self.connected = False

    # ------------------------------------------------------------ транспорт

    def connect(self) -> None:
        import paho.mqtt.client as mqtt

        # paho 2.0 потребовал явно выбирать версию API обратных вызовов:
        # конструктор без неё просто падает. VERSION1 — это ровно те
        # сигнатуры, что объявлены ниже, поэтому одной ветки хватает на обе
        # системы: в Bookworm лежит paho 1.6, в Trixie уже 2.x.
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                                 client_id=self.client_id, clean_session=True)
        else:
            client = mqtt.Client(client_id=self.client_id, clean_session=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # Переподключение само: п.9 требует, чтобы после выключения роутера
        # на минуту блок вернулся в строй без вмешательства.
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(self.host, self.port, keepalive=60)
        client.loop_start()
        self._client = client

    def _on_connect(self, client, _userdata, _flags, code) -> None:
        self.connected = code == 0
        if self.connected:
            for topic in SUBSCRIPTIONS:
                client.subscribe(topic, qos=0)

    def _on_disconnect(self, _client, _userdata, _code) -> None:
        self.connected = False

    def _on_message(self, _client, _userdata, message) -> None:
        self._pending.append((message.topic, message.payload.decode("utf-8", "replace"),
                              bool(message.retain)))

    # ----------------------------------------------------------- источник

    def poll(self, state: State) -> bool:
        if self._client is None:
            self.connect()

        state.health.mqtt_ok = self.connected
        if not self._pending:
            return False

        batch, self._pending = self._pending, []
        changed = False
        for topic, payload, retained in batch:
            changed |= apply_message(state, topic, payload, state.now, retained)
        return changed

    def publish(self, topic: str, payload: str, retain: bool = True) -> None:
        if self._client is not None:
            self._client.publish(topic, payload, qos=0, retain=retain)

    def close(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
