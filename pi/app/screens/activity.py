"""Режим 2: активность за ПК (п.10 плана). Источник — ПК-агент через MQTT."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..state import State
from . import widgets as w
from .base import Screen

CATEGORIES = {
    "code": ("Код", theme.ACCENT),
    "game": ("Игра", (200, 150, 255)),
    "browser": ("Браузер", theme.OK),
    "other": ("Прочее", theme.DIM),
}


class ActivityScreen(Screen):
    name = "activity"
    title = "Активность"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        pc = state.pc

        if not state.pc_online:
            w.header(draw, width, self.title, "нет связи", theme.ALERT, state=state)
            w.empty_state(draw, width, height, "ПК офлайн",
                          "heartbeat не приходит — агент не запущен или сеть отвалилась")
            return

        label, color = CATEGORIES.get(pc.category, CATEGORIES["other"])
        w.header(draw, width, self.title, "AFK" if pc.afk else None, color, state=state)

        draw.text((width / 2, 96), label, font=theme.font(theme.H1, bold=True),
                  fill=color, anchor="mm")
        draw.text((width / 2, 136), pc.active_app or "—",
                  font=theme.font(theme.BODY), fill=theme.DIM, anchor="mm")

        boxes = w.row(width, 176, height - w.PAD, 3)
        w.stat_card(draw, boxes[0], str(pc.keystrokes), "нажатий за минуту")
        w.stat_card(draw, boxes[1], str(pc.mouse_clicks), "кликов за минуту")
        w.stat_card(draw, boxes[2],
                    "вкл" if pc.audio_active else "выкл", "звук",
                    theme.OK if pc.audio_active else theme.DIM)
