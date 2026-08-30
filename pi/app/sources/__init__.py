"""Источники данных: всё, что наполняет State.

Пакет заменил прежний модуль app/sources.py. Интерфейс стал строже —
у источника есть период опроса и изоляция отказов, — потому что с живым
железом это обязательно: отвалившийся по I2C датчик не должен ронять часы,
а упавший запрос погоды не должен ждать следующей попытки четверть часа.

Имена, на которые опирался прежний модуль, реэкспортируются отсюда.
"""

from .base import Source
from .desk import AnomalySource, ManualResetSource, StreakSource
from .fake import FakeEnvSource, FakePcSource, FakePresenceSource
from .system import SystemHealthSource
from .weather import WeatherSource

__all__ = [
    "Source",
    "AnomalySource", "ManualResetSource", "StreakSource",
    "FakeEnvSource", "FakePcSource", "FakePresenceSource",
    "SystemHealthSource", "WeatherSource",
]


def fake_bundle() -> list[Source]:
    """Набор для разработки без железа: сервис оживает целиком."""
    return [FakePresenceSource(), FakeEnvSource(), FakePcSource(), ManualResetSource()]
