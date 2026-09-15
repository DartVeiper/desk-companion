"""Погода и прогноз дождя через Open-Meteo.

Ключ не нужен — и это отменяет отдельный пункт плана. П.6 велел заранее
регистрироваться в API Яндекс.Погоды ради nowcast, но Open-Meteo отдаёт
осадки по 15-минутным интервалам бесплатно и без регистрации. У Яндекса
разрешение поминутное, здесь по четверть часа; для предупреждения
«дождь через 20-30 минут» на настольных часах разницы нет.

Сеть — вещь ненадёжная, поэтому последнее удачное значение держим и
показываем даже когда запрос упал: часы с прочерком вместо погоды хуже,
чем часы со слегка устаревшей погодой.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime

from .. import forecast
from ..state import State
from .base import Source

API = "https://api.open-meteo.com/v1/forecast"

# Порог, ниже которого «осадки» — это морось в данных, а не дождь на улице.
RAIN_MM = 0.15
# Дальше этого не предупреждаем: смысл строки в том, что зонт нужен сейчас.
HORIZON_MINUTES = 90

# Коды WMO. Схлопнуты до того, что влезает в строку под часами.
CODES = {
    0: "ясно", 1: "малооблачно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "морось", 53: "морось", 55: "морось",
    56: "ледяная морось", 57: "ледяная морось",
    61: "дождь", 63: "дождь", 65: "ливень",
    66: "ледяной дождь", 67: "ледяной дождь",
    71: "снег", 73: "снег", 75: "снегопад", 77: "снежная крупа",
    80: "ливень", 81: "ливень", 82: "сильный ливень",
    85: "снегопад", 86: "снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}


class WeatherSource(Source):
    name = "weather"

    def __init__(self, latitude: float, longitude: float,
                 interval: float = 900, timeout: float = 12) -> None:
        super().__init__()
        self.latitude = latitude
        self.longitude = longitude
        self.interval = interval
        self.timeout = timeout
        self.updated_at: datetime | None = None

    def url(self) -> str:
        return (
            f"{API}?latitude={self.latitude}&longitude={self.longitude}"
            "&current=temperature_2m,weather_code,is_day"
            "&minutely_15=precipitation&forecast_minutely_15=8"
            # Двое суток по часам — этого хватает на три части суток вперёд
            # даже поздно вечером, когда всё интересное уже завтра.
            "&hourly=temperature_2m,weather_code&forecast_days=2"
            "&timezone=auto"
        )

    def poll(self, state: State) -> bool:
        with urllib.request.urlopen(self.url(), timeout=self.timeout) as response:
            data = json.loads(response.read())

        current = data["current"]
        state.weather.temp = float(current["temperature_2m"])
        code = int(current["weather_code"])
        state.weather.code = code
        state.weather.cond = CODES.get(code, "")
        state.weather.is_day = bool(int(current.get("is_day", 1)))
        state.weather.rain_soon_minutes = rain_in_minutes(data.get("minutely_15"))

        hourly = data.get("hourly") or {}
        state.weather.ahead = forecast.parts(
            hourly.get("time", []),
            hourly.get("temperature_2m", []),
            hourly.get("weather_code", []),
            datetime.now(),
        )
        self.updated_at = datetime.now()
        return True

    def on_failure(self, state: State) -> bool:
        # Значения не трогаем: пусть висит последнее удачное. Факт отказа
        # виден через self.ok — его подхватит строка состояния.
        return False


def rain_in_minutes(minutely: dict | None, now: datetime | None = None) -> int | None:
    """Через сколько минут ожидается дождь. None — не ожидается.

    Отдаём начало интервала: обещать «через 7 минут» по данным с шагом в
    четверть часа — ложная точность.
    """
    if not minutely:
        return None
    now = now or datetime.now()
    for stamp, amount in zip(minutely.get("time", []), minutely.get("precipitation", [])):
        if amount is None or amount < RAIN_MM:
            continue
        when = datetime.fromisoformat(stamp)
        minutes = round((when - now).total_seconds() / 60)
        if minutes < 0:
            continue  # интервал уже идёт
        if minutes > HORIZON_MINUTES:
            return None  # дальше по времени — уже не новость
        return minutes
    return None
