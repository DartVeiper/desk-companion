"""Настройки на самом устройстве. Вход — удержание 3 секунды.

Состав намеренно скудный. Крутить настройки энкодером на 480x320 мучительно,
а веб-дашборд всё равно будет — поэтому здесь живёт только то, что нужно,
когда веб недоступен: яркость и диагностика. Пороги, порядок экранов и
стили остаются в браузере.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..inputs.events import Action
from ..state import State
from . import widgets as w
from .base import DetailScreen, Screen

BACK_HINT = "нажать — назад"


class BrightnessScreen(DetailScreen):
    """Яркость подсветки. PWM на GPIO18."""

    name = "brightness"
    title = "Яркость"

    STEP = 5
    MIN = 10  # ниже подсветка гаснет неравномерно, а экран уже не читается

    def handle(self, action: Action, state: State) -> bool:
        if action in (Action.NEXT, Action.PREV):
            step = self.STEP if action is Action.NEXT else -self.STEP
            state.brightness = max(self.MIN, min(100, state.brightness + step))
            return True
        return False

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        w.header(draw, width, self.title, BACK_HINT, theme.ACCENT)

        draw.text((width / 2, 150), f"{state.brightness}%",
                  font=theme.font(88, weight=theme.EXTRABOLD), fill=theme.FG, anchor="ms")

        bar = (theme.PAD + 20, 182, width - theme.PAD - 20, 204)
        draw.rounded_rectangle(bar, radius=11, fill=theme.SURFACE)
        filled = (state.brightness - self.MIN) / (100 - self.MIN)
        draw.rounded_rectangle((bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * filled, bar[3]),
                               radius=11, fill=theme.ACCENT)

        draw.text((width / 2, 236), "поворот — меняет",
                  font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")
        draw.text((width / 2, 266), "ночью гаснет сама, если включён ночной режим",
                  font=theme.font(theme.TINY), fill=theme.LINE, anchor="mm")


class DiagnosticsScreen(DetailScreen):
    """Полная картина здоровья блока: то, что строка состояния сжимает в значок."""

    name = "diagnostics"
    title = "Диагностика"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        h = state.health
        w.header(draw, width, self.title, BACK_HINT,
                 theme.ALERT if state.problems() else theme.OK)

        rows = [
            ("WiFi", h.wifi_ssid or "нет сети", h.wifi_ok),
            ("сигнал", "—" if h.wifi_signal_dbm is None else f"{h.wifi_signal_dbm} dBm", h.wifi_ok),
            ("адрес", h.ip or "—", bool(h.ip)),
            ("MQTT", "работает" if h.mqtt_ok else "не отвечает", h.mqtt_ok),
            ("SCD41", "отвечает" if h.scd41_ok else "молчит", h.scd41_ok),
            ("LD2410", "отвечает" if h.ld2410_ok else "молчит", h.ld2410_ok),
            ("питание", "просадки" if h.throttled else "норма", not h.throttled),
            ("карта", f"{h.disk_free_pct:.0f}% свободно", h.disk_free_pct >= 10),
            ("темп. Pi", "—" if h.cpu_temp is None else f"{h.cpu_temp:.0f}°",
             h.cpu_temp is None or h.cpu_temp < 75),
            ("аптайм", w.duration(h.uptime_seconds), True),
            ("ПК-агент", "онлайн" if state.pc_online else "офлайн", state.pc_online),
        ]

        cols, per_col = 2, (len(rows) + 1) // 2
        col_w = (width - theme.PAD * 2 - w.GAP) / cols
        for i, (name, value, ok) in enumerate(rows):
            x = theme.PAD + (i // per_col) * (col_w + w.GAP)
            y = 68 + (i % per_col) * 22
            draw.ellipse((x, y - 3, x + 6, y + 3), fill=theme.OK if ok else theme.ALERT)
            draw.text((x + 14, y), name, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")
            draw.text((x + col_w - 4, y), value,
                      font=w.fit_font(draw, value, col_w - 84, theme.TINY, bold=True, min_size=10),
                      fill=theme.FG if ok else theme.ALERT, anchor="rm")


class SettingsScreen(Screen):
    """Меню настроек. Не входит в карусель — только по удержанию 3 секунды."""

    name = "settings"
    title = "Настройки"

    def __init__(self) -> None:
        self.details = [BrightnessScreen(), DiagnosticsScreen()]
        self._index = 0

    @property
    def _items(self) -> list[tuple[str | None, str]]:
        return [(d.name, d.title) for d in self.details] + [(None, "Выйти")]

    def handle(self, action: Action, state: State) -> bool:
        items = self._items
        if action is Action.NEXT:
            self._index = (self._index + 1) % len(items)
            return True
        if action is Action.PREV:
            self._index = (self._index - 1) % len(items)
            return True
        if action is Action.SELECT:
            target = items[self._index][0]
            if target is None:
                self.exit_requested = True
            else:
                self.requested_detail = target
            return True
        return False

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        w.header(draw, width, self.title, "остальное — в браузере", theme.ACCENT, state=state)

        items = self._items
        top, gap = 74, 10
        cell_h = (height - top - theme.PAD - gap * (len(items) - 1)) / len(items)
        for i, (_, label) in enumerate(items):
            y = top + i * (cell_h + gap)
            box = (theme.PAD, y, width - theme.PAD, y + cell_h)
            w.card(draw, box)
            if i == self._index:
                draw.rounded_rectangle(box, radius=14, outline=theme.ACCENT, width=3)
            draw.text((box[0] + 24, (box[1] + box[3]) / 2), label,
                      font=theme.font(theme.BODY, bold=True), fill=theme.FG, anchor="lm")
            if i == self._index:
                draw.text((box[2] - 24, (box[1] + box[3]) / 2), "нажать",
                          font=theme.font(theme.TINY), fill=theme.ACCENT, anchor="rm")
