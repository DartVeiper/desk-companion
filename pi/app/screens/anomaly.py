"""Режим 6: индикатор необычной сессии (п.10 плана).

План требует прямо: индикатор должен читаться даже мельком, крупной иконкой
и цветом, а не только текстом. Поэтому здесь полполя занимает круг, и его
цвет виден боковым зрением, без чтения.

Данные появятся после шагов 10-11: сначала 1-2 недели наблюдений в
activity_minute, только потом обученный IsolationForest.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..draw_utils import smooth
from ..state import State
from . import widgets as w
from .base import Screen


class AnomalyScreen(Screen):
    name = "anomaly"
    title = "Аномалия"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        pc = state.pc

        if not state.pc_online:
            w.header(draw, width, self.title, "нет связи", theme.ALERT, state=state)
            w.empty_state(draw, width, height, "ПК офлайн", "модель считает на данных агента")
            return

        flagged = pc.anomaly_flag
        color = theme.ALERT if flagged else theme.OK
        w.header(draw, width, self.title, None, color, state=state)

        cx, cy, r = width // 2, 158, 62

        def paint(d: ImageDraw.ImageDraw, s: int) -> None:
            c = r + 6
            d.ellipse(((c - r) * s, (c - r) * s, (c + r) * s, (c + r) * s),
                      outline=color, width=6 * s)

        badge = smooth(((r + 6) * 2, (r + 6) * 2), paint)
        frame.paste(badge, (cx - r - 6, cy - r - 6), badge)
        draw.text((cx, cy), "!" if flagged else "OK",
                  font=theme.font(64 if flagged else 40, bold=True), fill=color, anchor="mm")

        headline = "Необычная сессия" if flagged else "Всё как обычно"
        draw.text((cx, 248), headline, font=theme.font(theme.H2, bold=True),
                  fill=color, anchor="mm")

        reason = pc.anomaly_reason if flagged else "модель не видит отклонений"
        draw.text((cx, 282), reason, font=theme.font(theme.SMALL),
                  fill=theme.DIM, anchor="mm")
