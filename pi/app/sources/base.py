"""Источники данных: всё, что наполняет State.

Один интерфейс на датчик, погоду, MQTT и системное здоровье. Главный цикл
не знает, что именно опрашивает — поэтому подключение настоящего SCD41
позже сводится к замене одного класса, а не к правке цикла.

Каждый источник сам говорит, как часто его дёргать: погоду раз в 15 минут,
радар — постоянно.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..state import State


class Source(ABC):
    #: Секунд между опросами.
    interval: float = 1.0
    #: Имя для логов и диагностики.
    name: str = ""
    #: Через сколько повторить после неудачи. Дальше удваивается, но не
    #: превышает interval. Нужно из-за медленных источников: если первый
    #: запрос погоды упал по таймауту, ждать полные 15 минут — значит
    #: держать на экране прочерк всё это время после включения.
    retry_after: float = 15.0

    def __init__(self) -> None:
        self._next_at = 0.0
        self._backoff = 0.0
        self.last_error: str | None = None
        self.ok = True
        self.failures = 0

    def due(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) >= self._next_at

    def tick(self, state: State) -> bool:
        """Опросить, если пришло время. True — состояние изменилось.

        Исключение источника не должно ронять весь сервис: часы обязаны
        показывать время, даже когда датчик отвалился. Ошибку запоминаем,
        её покажет строка состояния.
        """
        now = time.monotonic()
        if now < self._next_at:
            return False
        try:
            changed = self.poll(state)
            self.ok, self.last_error, self.failures = True, None, 0
            self._backoff = 0.0
            self._next_at = now + self.interval
            return bool(changed)
        except Exception as exc:  # noqa: BLE001 — падать нельзя, см. выше
            self.ok = False
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._backoff = min(self.interval,
                                self.retry_after if not self._backoff else self._backoff * 2)
            self._next_at = now + self._backoff
            return self.on_failure(state)

    @abstractmethod
    def poll(self, state: State) -> bool:
        """Обновить состояние. Вернуть True, если что-то поменялось."""

    def on_failure(self, state: State) -> bool:
        """Что пометить в состоянии при ошибке. По умолчанию — ничего."""
        return False

    def close(self) -> None:
        pass
