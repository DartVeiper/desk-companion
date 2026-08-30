"""Абстракция ввода.

Источники (энкодер, тач, позже — голос или команда по MQTT) публикуют
одинаковые события. Экраны не знают, откуда пришло действие, поэтому
добавление нового способа ввода не требует правок в экранах.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from enum import Enum, auto


class Action(Enum):
    NEXT = auto()    # следующий экран
    PREV = auto()    # предыдущий экран
    SELECT = auto()  # подтвердить выбор
    BACK = auto()    # отмена
    HOLD = auto()    # долгое нажатие -> Manual Override, режим 4 (п.10 плана)
    SETTINGS = auto()  # очень долгое нажатие -> настройки
    TAP = auto()     # касание с координатами; решение принимает не тач, а экран


@dataclass(frozen=True)
class InputEvent:
    action: Action
    source: str  # "encoder" | "touch" | ...
    ts: float    # monotonic, для отладки задержек
    # Заполняются только у TAP. Энкодер координат не имеет, и это нормально:
    # всё, что делается тапом по блоку, обязано делаться и с энкодера тоже.
    x: int | None = None
    y: int | None = None


class EventBus:
    """Потокобезопасная очередь: источники пишут, главный цикл читает."""

    def __init__(self, maxsize: int = 32) -> None:
        self._q: queue.Queue[InputEvent] = queue.Queue(maxsize=maxsize)

    def emit(self, action: Action, source: str,
             x: int | None = None, y: int | None = None) -> None:
        try:
            self._q.put_nowait(InputEvent(action, source, time.monotonic(), x, y))
        except queue.Full:
            # Очередь забита — значит главный цикл завис. Копить лаг ввода
            # хуже, чем потерять событие: иначе экран потом «догоняет»
            # десятком щелчков подряд.
            pass

    def poll(self, timeout: float | None = None) -> InputEvent | None:
        try:
            if timeout is None:
                return self._q.get_nowait()
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None
