"""Режим 7: стрик привычек (п.10 плана).

Дней подряд, в каждом из которых был перерыв в каждом двухчасовом окне.
Считается на Pi поверх activity_minute, ПК-агент тут ни при чём.
"""

from __future__ import annotations

from datetime import timedelta

from PIL import Image, ImageDraw

from .. import theme
from ..state import State
from . import widgets as w
from .base import Screen

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _plural(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return "день подряд"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return "дня подряд"
    return "дней подряд"


class StreakScreen(Screen):
    name = "streak"
    title = "Стрик привычек"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        days = state.desk.streak_days
        color = theme.OK if days > 0 else theme.DIM

        w.header(draw, width, self.title, "перерыв каждые 2 часа", color, state=state)

        draw.text((width / 2, 148), str(days), font=theme.font(104, bold=True),
                  fill=color, anchor="ms")
        draw.text((width / 2, 180), _plural(days), font=theme.font(theme.BODY),
                  fill=theme.DIM, anchor="mm")

        # Последние семь дней, сегодня справа. Неделя от понедельника тут
        # обманывает: при стрике в 4 дня, начатом в выходные, часть закрашенных
        # клеток уезжала за границу недели и на экране их было меньше числа.
        cell = (width - w.PAD * 2 - w.GAP * 6) / 7
        top = 214
        for i in range(7):
            x = w.PAD + i * (cell + w.GAP)
            box = (x, top, x + cell, top + cell)
            ago = 6 - i  # 0 — сегодня
            day = state.now - timedelta(days=ago)
            done = ago < days
            w.card(draw, box, (26, 58, 34) if done else theme.SURFACE)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2 - 6
            if done:
                w.check_mark(draw, cx, cy, 18, theme.OK)
            else:
                draw.text((cx, cy), "·", font=theme.font(26, bold=True),
                          fill=theme.LINE, anchor="mm")
            draw.text((cx, box[3] - 14), WEEKDAYS[day.weekday()],
                      font=theme.font(theme.TINY),
                      fill=theme.FG if ago == 0 else theme.DIM, anchor="mm")
