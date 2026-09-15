"""Прогноз на ближайшие части суток.

Три колонки: что будет в ближайшую часть суток, следующую и ту, что за
ней. Прошедшее не показываем — в пять вечера сегодняшнее утро занимает
место и не говорит ничего.

Почему не почасовой график. На экране 480x320 двадцать четыре столбика
превращаются в частокол, который надо разглядывать. А вопрос, ради
которого на прогноз смотрят, звучит «брать ли зонт вечером» — и ответ на
него это три числа и три картинки, а не сорок восемь точек.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..sources.weather import CODES
from ..state import State
from . import weather_icons as icons
from . import widgets as w
from .base import Screen


class ForecastScreen(Screen):
    name = "forecast"
    title = "Прогноз"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        ahead = state.weather.ahead
        w.header(draw, width, "Что будет дальше", dot=theme.ACCENT)

        if not ahead:
            w.empty_state(draw, width, height, "Прогноза нет",
                          "Pi не смог достучаться до погодного API")
            return

        for box, part in zip(w.row(width, 62, height - w.PAD, len(ahead)), ahead):
            self._column(draw, box, part)

    @staticmethod
    def _column(draw: ImageDraw.ImageDraw, box, part) -> None:
        w.card(draw, box)
        left, top, right, bottom = box
        middle = (left + right) / 2

        # Подпись обрезаем по ширине карточки: «завтра вечером» длиннее,
        # чем «днём», а колонок может быть три.
        font = theme.font(theme.SMALL, bold=True)
        draw.text((middle, top + 24),
                  w.ellipsize(draw, part.title, right - left - 14, font),
                  font=font, fill=theme.FG, anchor="mm")

        size = 64
        icons.draw_icon(draw, middle - size / 2, top + 44, size,
                        part.code, part.is_day, back=theme.SURFACE)

        draw.text((middle, top + 138), f"{part.temp:+.0f}°",
                  font=theme.font(theme.H1, weight=theme.EXTRABOLD),
                  fill=theme.FG, anchor="mm")

        caption = CODES.get(part.code, "")
        if caption:
            small = theme.font(theme.TINY)
            draw.text((middle, bottom - 20),
                      w.ellipsize(draw, caption, right - left - 12, small),
                      font=small, fill=theme.DIM, anchor="mm")
