"""Поддельные датчики: сервис оживает до того, как приедет железо.

Значения не случайные, а привязанные ко времени суток — иначе экран
дёргается, а по такой картинке нельзя судить, нормально ли выглядит
интерфейс в реальной жизни. Когда приедут SCD41 и LD2410, эти два класса
меняются на настоящие, а главный цикл не трогается вовсе.
"""

from __future__ import annotations

import math
import random
from datetime import datetime

from ..state import State
from .base import Source

WORK_HOURS = (9, 13), (14, 19), (20, 23)


def _at_desk(now: datetime, rng: random.Random) -> bool:
    hour = now.hour + now.minute / 60
    if not any(a <= hour < b for a, b in WORK_HOURS):
        return False
    # Перерыв примерно раз в час на несколько минут — чтобы ambient-режим
    # и расчёт стрика было на чём проверять.
    return not (7 <= now.minute < 7 + rng.randint(4, 12))


class FakePresenceSource(Source):
    name = "presence(fake)"
    interval = 2.0

    def __init__(self, seed: int = 42) -> None:
        super().__init__()
        self._rng = random.Random(seed)

    def poll(self, state: State) -> bool:
        now = state.now
        # Зерно от часа: внутри часа решение стабильно, а не мигает.
        rng = random.Random(now.hour * 100 + now.day)
        present = _at_desk(now, rng)
        if present == state.desk.presence:
            return False
        state.desk.presence = present
        state.desk.presence_since = now
        return True


class FakeEnvSource(Source):
    name = "env(fake)"
    interval = 10.0

    def __init__(self) -> None:
        super().__init__()
        self._co2 = 620.0

    def poll(self, state: State) -> bool:
        now = state.now
        # CO2 ползёт вверх при людях и оседает без них, со скоростью
        # близкой к реальной: около +200 ppm в час на одного человека.
        drift = 3.4 if state.desk.presence else -1.2
        self._co2 = min(1750.0, max(430.0, self._co2 + drift * self.interval / 60))

        hour = now.hour + now.minute / 60
        state.env.co2 = int(self._co2)
        state.env.temperature = round(22.4 + 1.6 * math.sin((hour - 4) / 24 * 2 * math.pi), 1)
        state.env.humidity = round(41 + self._co2 / 300, 1)
        state.env.updated = now
        return True


class FakePcSource(Source):
    """ПК-агент до того, как он написан. Шаг 6 заменит на подписку MQTT.

    Оживляет Режимы 2, 5 и 6 — без него они всё время показывают «ПК офлайн»,
    и вёрстку с настоящими числами не проверить.
    """

    name = "pc(fake)"
    interval = 2.0
    APPS = (("rider64.exe", "code"), ("chrome.exe", "browser"), ("cs2.exe", "game"))

    def __init__(self) -> None:
        super().__init__()
        self._elapsed = 0.0
        self._app = 0

    def poll(self, state: State) -> bool:
        self._elapsed += self.interval
        if self._elapsed % 45 < self.interval:
            self._app = (self._app + 1) % len(self.APPS)

        app, category = self.APPS[self._app]
        pc = state.pc
        pc.active_app, pc.category = app, category
        pc.keystrokes = random.randint(20, 220) if category == "code" else random.randint(0, 60)
        pc.mouse_clicks = random.randint(5, 70)
        pc.audio_active = category != "code"
        pc.gpu_load = round(30 + 60 * abs(math.sin(self._elapsed / 30)))
        pc.gpu_temp = round(55 + 25 * abs(math.sin(self._elapsed / 30)))
        pc.cpu_load = round(15 + 40 * abs(math.sin(self._elapsed / 21)))
        pc.cpu_temp = round(45 + 20 * abs(math.sin(self._elapsed / 21)))
        pc.last_heartbeat = state.now
        return True
