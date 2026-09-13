"""Копилка уровней энергии радара по зонам дальности.

Зачем она есть. Пороги радара подбираются по двум числам на зону: каким
бывает фон пустой комнаты и каким — присутствие человека. Очевидный способ
их получить — поставить опыт: выйти из комнаты на несколько минут, потом
вернуться и посидеть неподвижно. Способ рабочий, но у него два изъяна.

Первый: опыт надо провести, а человек занят. Второй, куда неприятнее, —
четыре минуты записи портит одна случайность. Первый же наш замер «пустой
комнаты» оказался снят при человеке в ней, и понять это по числам было
можно только задним числом.

Здесь другой подход. Блок работает круглосуточно, и комната за сутки
бывает и пустой, и занятой — сама, без всякого опыта. Достаточно всё это
время считать, сколько раз каждая зона показывала каждый уровень энергии,
и в накопленном распределении обе картины найдутся: нижние доли отвечают
пустой комнате, верхние — присутствию. Чем дольше копится, тем надёжнее.

Хранится это гистограммой, а не списком замеров: девять зон на два вида
энергии на сто один уровень — это 1818 чисел независимо от того, копим мы
час или месяц.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .drivers.ld2410 import GATES

#: Энергия у LD2410 — целое от 0 до 100 включительно.
LEVELS = 101


class Levels:
    """Сколько раз каждая зона показывала каждый уровень энергии."""

    def __init__(self) -> None:
        self.moving = [[0] * LEVELS for _ in range(GATES)]
        self.static = [[0] * LEVELS for _ in range(GATES)]
        self.samples = 0
        self.started = time.time()

    # ------------------------------------------------------------ запись

    def add(self, moving: list[int], static: list[int]) -> None:
        for gate in range(min(GATES, len(moving))):
            value = moving[gate]
            if 0 <= value < LEVELS:
                self.moving[gate][value] += 1
        for gate in range(min(GATES, len(static))):
            value = static[gate]
            if 0 <= value < LEVELS:
                self.static[gate][value] += 1
        self.samples += 1

    # ------------------------------------------------------------- счёт

    @staticmethod
    def _percentile(counts: list[int], share: float) -> int:
        """Уровень, ниже которого лежит заданная доля замеров зоны."""
        total = sum(counts)
        if not total:
            return 0
        target = share * total
        seen = 0
        for level, count in enumerate(counts):
            seen += count
            if seen >= target:
                return level
        return LEVELS - 1

    def quantiles(self, gate: int, shares: tuple[float, ...]) -> dict:
        """Доли распределения зоны — по обоим видам энергии."""
        return {
            "moving": [self._percentile(self.moving[gate], s) for s in shares],
            "static": [self._percentile(self.static[gate], s) for s in shares],
        }

    @property
    def hours(self) -> float:
        """Сколько времени копится. Кадры идут примерно десять раз в секунду."""
        return self.samples / 10 / 3600

    # --------------------------------------------------------- хранение

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Через временный файл: выключение питания посреди записи не должно
        # оставлять обрезанный JSON, который потом не прочитается.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "moving": self.moving,
            "static": self.static,
            "samples": self.samples,
            "started": self.started,
        }), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Levels":
        levels = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return levels

        moving, static = data.get("moving"), data.get("static")
        if not isinstance(moving, list) or len(moving) != GATES:
            return levels
        levels.moving = [list(row) for row in moving]
        levels.static = [list(row) for row in static]
        levels.samples = int(data.get("samples", 0))
        levels.started = float(data.get("started", time.time()))
        return levels
