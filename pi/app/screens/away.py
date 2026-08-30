"""Режим 3: меня нет (п.10 плана). Радар LD2410 плюс эвристика времени суток.

Не путать с ambient-режимом: тот включается автоматически и показывает
крупные часы издалека, а этот — информационный, живёт в карусели и
отвечает на вопрос «сколько тебя уже нет».
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..state import State
from . import widgets as w
from .base import Screen

NIGHT_FROM, NIGHT_TO = 23, 7


class AwayScreen(Screen):
    name = "away"
    title = "Меня нет"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        desk = state.desk

        if desk.presence:
            w.header(draw, width, self.title, None, theme.OK, state=state)
            headline, color = "За столом", theme.OK
            caption = "радар видит присутствие"
        else:
            w.header(draw, width, self.title, None, theme.DIM, state=state)
            hour = state.now.hour
            night = hour >= NIGHT_FROM or hour < NIGHT_TO
            headline, color = ("Скорее всего сплю", theme.ACCENT) if night else ("Меня нет", theme.DIM)
            caption = "ночное окно" if night else "радар никого не видит"

        draw.text((width / 2, 116), headline, font=theme.font(theme.H1, bold=True),
                  fill=color, anchor="mm")
        draw.text((width / 2, 158), caption, font=theme.font(theme.SMALL),
                  fill=theme.DIM, anchor="mm")

        since = desk.presence_since
        elapsed = w.duration((state.now - since).total_seconds()) if since else "—"

        boxes = w.row(width, 196, height - w.PAD, 2)
        w.stat_card(draw, boxes[0], elapsed,
                    "в этом статусе" if since else "статус только что сменился", color)
        w.stat_card(draw, boxes[1],
                    desk.manual_status or "авто",
                    "ручной статус" if desk.manual_status else "ручной статус не задан",
                    theme.WARN if desk.manual_status else theme.DIM)
