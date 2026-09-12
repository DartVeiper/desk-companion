"""Режим 1: часы + погода (п.10 плана).

Секунды сознательно не показываем. Они заставляли бы перерисовывать экран
каждую секунду, а вся экономия на SPI построена на том, что кадр меняется
редко: смена минуты трогает несколько горизонтальных полос из шестнадцати.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..state import State
from . import widgets as w
from .base import Screen

_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
)
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def date_text(state: State) -> str:
    return (
        f"{_WEEKDAYS[state.now.weekday()]}, "
        f"{state.now.day} {_MONTHS[state.now.month - 1]}"
    )


def status(state: State) -> tuple[str, tuple[int, int, int]]:
    if state.desk.manual_status:
        return state.desk.manual_status, theme.WARN
    return ("за столом", theme.OK) if state.desk.presence else ("никого", theme.DIM)


def cells(state: State) -> list[tuple[str, str, tuple[int, int, int]]]:
    out, env = state.weather, state.env
    # Подпись всегда называет место. Иначе «облачно» под числом читается
    # как показание датчика, а не как погода за окном.
    weather_caption = f"на улице  {out.cond}" if out.cond else "на улице"
    room_caption = "в комнате" if env.humidity is None else f"в комнате  {env.humidity:.0f}%"
    return [
        ("--" if out.temp is None else f"{out.temp:+.0f}°",
         weather_caption, theme.DIM if out.temp is None else theme.FG),
        ("--" if env.co2 is None else str(env.co2),
         "CO2, ppm", theme.co2_color(env.co2)),
        ("--" if env.temperature is None else f"{env.temperature:.1f}°",
         room_caption, theme.DIM if env.temperature is None else theme.FG),
    ]


class ClockScreen(Screen):
    name = "clock"
    title = "Часы + погода"

    def __init__(self) -> None:
        from .details import AirDetail, WeatherDetail

        # Нужны для тапа по карточке: погода и воздух открываются
        # пальцем прямо отсюда. Те же экраны стоят и в карусели —
        # это разные пути к одному месту, и оба уместны: пальцем
        # тыкают в то, что видят, а крутилкой листают подряд.
        self.details = [WeatherDetail(), AirDetail()]

    @staticmethod
    def card_boxes(width: int, height: int) -> list[tuple[float, float, float, float]]:
        """Геометрия карточек в одном месте: по ней и рисуем, и ловим тапы."""
        return w.row(width, 198, height - w.PAD, 3)

    def hit_zones(self, width: int, height: int) -> dict[str, tuple[float, float, float, float]]:
        weather, co2, room = self.card_boxes(width, height)
        # Обе правые карточки ведут в один экран воздуха: CO2, температура и
        # влажность приходят с одного SCD41, разводить их по разным экранам
        # незачем.
        return {"weather_detail": weather, "air_detail": (co2[0], co2[1], room[2], room[3])}

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        label, color = status(state)
        w.header(draw, width, label, None if state.pc_online else "ПК офлайн", color, state=state)

        # Часы по базовой линии (anchor="ms"), а не по центру: у шрифта 132 px
        # строка заметно выше самих цифр, и дата налезала на них.
        draw.text((width / 2, 148), state.now.strftime("%H:%M"),
                  font=theme.font(theme.CLOCK, bold=True), fill=theme.FG, anchor="ms")

        rain = state.weather.rain_soon_minutes
        subtitle = date_text(state)
        draw.text((width / 2, 176), subtitle, font=theme.font(theme.SMALL),
                  fill=theme.DIM, anchor="mm")
        if rain is not None:
            draw.text((width - theme.PAD, 176), f"дождь через {rain} мин",
                      font=theme.font(theme.TINY), fill=theme.ACCENT, anchor="rm")

        for box, (value, caption, cell_color) in zip(self.card_boxes(width, height), cells(state)):
            w.stat_card(draw, box, value, caption, cell_color)
