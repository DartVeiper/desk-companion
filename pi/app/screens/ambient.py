"""Ambient-режим: что показывает блок, когда за столом никого нет.

Это состояние, а не восьмой экран карусели — энкодером сюда не долистать.
Радар теряет присутствие, выдерживается пауза, экран уходит в крупные часы,
читаемые с другого конца комнаты. Присутствие вернулось — мгновенно обратно
на тот режим, где вы были.

Данных здесь намеренно почти нет: издалека нужно только время.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..draw_utils import clock_face, dot_matrix, hand, smooth
from ..state import State
from .base import Screen


class AmbientScreen(Screen):
    """Метка: экран покоя, в карусель режимов не попадает."""


class NightRedAmbient(AmbientScreen):
    """Ночь: красным по чёрному, лучи по всему полю.

    Красный выбран не для красоты — он меньше всего сбивает адаптацию глаза
    к темноте. Вместе с приглушённой подсветкой (PWM на GPIO18) это решает
    проблему четырёхдюймовой лампы в спальне в три часа ночи.
    """

    name = "ambient_night"
    title = "Ночной красный"

    RED = (255, 56, 40)
    RED_DIM = (150, 28, 20)

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        hour_angle = (state.now.hour % 12) * 30 + state.now.minute * 0.5
        minute_angle = state.now.minute * 6

        def paint(d: ImageDraw.ImageDraw, s: int) -> None:
            cx, cy = w * s / 2, h * s / 2
            rx, ry = w * s / 2 - 5 * s, h * s / 2 - 5 * s
            clock_face(d, cx, cy, rx, ry, s, self.RED, self.RED_DIM,
                       theme.font(int(44 * s), bold=True))

            hand(d, cx, cy, hour_angle, ry * 0.46, 13 * s, self.RED)
            hand(d, cx, cy, minute_angle, ry * 0.70, 10 * s, self.RED)
            # Кольцо в центре, а не точка: на референсе стрелки сходятся
            # в маленькую окружность, и это заметно опрятнее жирной кляксы.
            d.ellipse((cx - 9 * s, cy - 9 * s, cx + 9 * s, cy + 9 * s),
                      fill=(0, 0, 0), outline=self.RED, width=4 * s)

        face = smooth((w, h), paint, scale=3)
        frame.paste(face, (0, 0), face)

        # Дата и будильник по бокам от центра, как на референсе. Стрелки их
        # иногда перечёркивают — на настоящих часах происходит ровно то же.
        draw.text((w // 2 - 66, h // 2), state.now.strftime("%a %d").upper(),
                  font=theme.font(theme.TINY, bold=True), fill=self.RED, anchor="rm")


class BigDigitsAmbient(AmbientScreen):
    """День: огромные цифры, ничего лишнего. Референс — StandBy в iPhone."""

    name = "ambient_digits"
    title = "Крупные цифры"

    PALETTE = (
        (108, 220, 132),   # зелёный
        (232, 110, 190),   # розовый
        (120, 180, 255),   # голубой
        (244, 170, 90),    # янтарный
    )

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        # Цвет меняется день ото дня, но в течение дня стоит на месте:
        # мигающая на глазах смена палитры раздражала бы.
        color = self.PALETTE[state.now.timetuple().tm_yday % len(self.PALETTE)]

        draw.text(
            (w // 2, 208), state.now.strftime("%H:%M"),
            font=theme.font(152, bold=True), fill=color, anchor="ms",
        )
        line = state.now.strftime("%d.%m")
        if state.weather.temp is not None:
            line += f"   ·   на улице {state.weather.temp:+.0f}°"
        draw.text((w // 2, 262), line, font=theme.font(theme.BODY), fill=theme.DIM, anchor="mm")


class MatrixAmbient(AmbientScreen):
    """Ретро: точечная матрица, как светодиодное табло."""

    name = "ambient_matrix"
    title = "Точечная матрица"

    GREEN = (150, 240, 170)

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size

        digits = dot_matrix(
            state.now.strftime("%H:%M"),
            theme.font(28, weight=theme.EXTRABOLD), self.GREEN,
            cell=7, max_width=w - 96,
        )
        frame.paste(digits, ((w - digits.width) // 2, 86), digits)

        hour = state.now.hour
        if hour < 5:
            greeting = "доброй ночи"
        elif hour < 12:
            greeting = "доброе утро"
        elif hour < 18:
            greeting = "добрый день"
        else:
            greeting = "добрый вечер"
        draw.text((w // 2, 224), greeting, font=theme.font(theme.BODY), fill=theme.DIM, anchor="mm")

        box = (w // 2 - 128, 254, w // 2 + 128, 292)
        draw.rounded_rectangle(box, radius=6, outline=theme.LINE, width=2)
        draw.text((box[0] + 14, 273), state.now.strftime("%Y.%m.%d"),
                  font=theme.font(theme.SMALL), fill=theme.FG, anchor="lm")
        draw.text((box[2] - 14, 273), state.now.strftime("%a").upper(),
                  font=theme.font(theme.SMALL, bold=True), fill=theme.FG, anchor="rm")
