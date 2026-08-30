"""Режим 5: монитор GPU/CPU (п.10 плана).

Компоновка по ориентиру AIDA64 SensorPanel: круглый индикатор загрузки по
центру, мелкие цифровые показатели рядом. Сам AIDA64 не используется —
данные идут от ПК-агента через LibreHardwareMonitor.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..draw_utils import ring, smooth
from ..state import State
from . import widgets as w
from .base import Screen

TEMP_WARN, TEMP_ALERT = 75, 85


def _temp_color(value: float | None) -> tuple[int, int, int]:
    if value is None:
        return theme.DIM
    if value >= TEMP_ALERT:
        return theme.ALERT
    if value >= TEMP_WARN:
        return theme.WARN
    return theme.OK


class HardwareScreen(Screen):
    name = "hardware"
    title = "GPU / CPU"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        pc = state.pc

        if not state.pc_online:
            w.header(draw, width, self.title, "нет связи", theme.ALERT, state=state)
            w.empty_state(draw, width, height, "ПК офлайн", "heartbeat не приходит")
            return

        if pc.gpu_load is None and pc.cpu_temp is None:
            # П.6 плана: без прав администратора LibreHardwareMonitor молча
            # отдаёт пустоту. Пишем это прямо, чтобы не искать несуществующий баг.
            w.header(draw, width, self.title, "нет данных", theme.WARN, state=state)
            w.empty_state(draw, width, height, "Датчики недоступны",
                          "агенту нужны права администратора — проверь задачу в Планировщике")
            return

        w.header(draw, width, self.title, None, theme.OK, state=state)

        load = (pc.gpu_load or 0) / 100.0
        color = _temp_color(pc.gpu_temp)
        cx, cy, r = 106, 172, 74

        def paint(d: ImageDraw.ImageDraw, s: int) -> None:
            box = (8 * s, 8 * s, (2 * r + 8) * s, (2 * r + 8) * s)
            ring(d, box, load, 12 * s, color, theme.LINE)

        gauge = smooth((2 * r + 16, 2 * r + 16), paint)
        frame.paste(gauge, (cx - r - 8, cy - r - 8), gauge)
        draw.text((cx, cy - 8), f"{pc.gpu_load:.0f}%" if pc.gpu_load is not None else "--",
                  font=theme.font(38, bold=True), fill=theme.FG, anchor="mm")
        draw.text((cx, cy + 24), "GPU", font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")

        left = 200
        cell_w = (width - left - w.PAD - w.GAP) / 2
        cell_h = (height - 74 - w.PAD - w.GAP) / 2
        cells = [
            (f"{pc.gpu_temp:.0f}°" if pc.gpu_temp is not None else "--", "GPU темп.", _temp_color(pc.gpu_temp)),
            (f"{pc.cpu_load:.0f}%" if pc.cpu_load is not None else "--", "CPU загрузка", theme.FG),
            (f"{pc.cpu_temp:.0f}°" if pc.cpu_temp is not None else "--", "CPU темп.", _temp_color(pc.cpu_temp)),
            (pc.category or "—", "сейчас", theme.DIM),
        ]
        for i, (value, caption, cell_color) in enumerate(cells):
            x = left + (i % 2) * (cell_w + w.GAP)
            y = 74 + (i // 2) * (cell_h + w.GAP)
            w.stat_card(draw, (x, y, x + cell_w, y + cell_h), value, caption,
                        cell_color, value_size=30)
