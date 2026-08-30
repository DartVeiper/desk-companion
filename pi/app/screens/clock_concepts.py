"""Концептуально другие Режимы 1 — не перестановка тех же блоков.

Файл временный, как и clock_variants.py: победитель переедет в clock.py.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..draw_utils import polar, ring, smooth
from ..state import State
from .base import Screen
from .clock_variants import _cells, _date, _status


def _frac(value: float | None, lo: float, hi: float) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _status_bar(state: State, draw: ImageDraw.ImageDraw, y: int = 32) -> None:
    label, color = _status(state)
    draw.ellipse((theme.PAD, y - 5, theme.PAD + 10, y + 5), fill=color)
    draw.text((theme.PAD + 20, y), label, font=theme.font(theme.SMALL), fill=theme.DIM, anchor="lm")


class HybridClock(Screen):
    """Д — гибрид: крупные часы как в А, данные в карточках как в Б."""

    name = "clock_hybrid"
    title = "Д - гибрид (крупные часы + карточки)"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        _status_bar(state, draw)
        # Часы ставим по базовой линии (anchor="ms"), а не по центру: у шрифта
        # 132 px строка сильно выше самих цифр, и дата налезала на них.
        draw.text(
            (w // 2, 148), state.now.strftime("%H:%M"),
            font=theme.font(theme.CLOCK, bold=True), fill=theme.FG, anchor="ms",
        )
        draw.text((w // 2, 176), _date(state), font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")

        pad, gap = 14, 10
        cells = _cells(state)
        cw = (w - pad * 2 - gap * (len(cells) - 1)) / len(cells)
        for i, (value, caption, color) in enumerate(cells):
            x0 = pad + i * (cw + gap)
            draw.rounded_rectangle((x0, 198, x0 + cw, h - pad), radius=14, fill=theme.SURFACE)
            draw.text((x0 + cw / 2, 234), value, font=theme.font(34, bold=True), fill=color, anchor="mm")
            draw.text((x0 + cw / 2, 276), caption, font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")


class RingsClock(Screen):
    """Е — кольца: данные дугами, язык умных часов."""

    name = "clock_rings"
    title = "Е - кольца"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        _status_bar(state, draw, y=28)
        draw.text(
            (w // 2, 96), state.now.strftime("%H:%M"),
            font=theme.font(106, bold=True), fill=theme.FG, anchor="mm",
        )
        draw.text((w // 2, 150), _date(state), font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")

        env, out = state.env, state.weather
        gauges = [
            (_frac(out.temp, -20, 35),
             f"{out.temp:+.0f}" if out.temp is not None else "--", "улица", theme.ACCENT),
            (_frac(env.co2, 400, 2000),
             str(env.co2) if env.co2 is not None else "--", "CO2", theme.co2_color(env.co2)),
            (_frac(env.temperature, 15, 30),
             f"{env.temperature:.0f}°" if env.temperature is not None else "--", "комната", theme.OK),
        ]
        # Подпись выносим под кольцо: внутри она жалась к числу и читалась хуже.
        r, cy = 45, 220
        for i, (frac, value, label, color) in enumerate(gauges):
            cx = w * (i + 1) / (len(gauges) + 1)

            def paint(d: ImageDraw.ImageDraw, s: int, _f: float = frac, _c=color) -> None:
                box = (10 * s, 10 * s, (2 * r + 10) * s, (2 * r + 10) * s)
                ring(d, box, _f, 7 * s, _c, theme.LINE)

            arc = smooth((2 * r + 20, 2 * r + 20), paint)
            frame.paste(arc, (int(cx - r - 10), int(cy - r - 10)), arc)
            draw.text((cx, cy), value, font=theme.font(30, bold=True), fill=theme.FG, anchor="mm")
            draw.text((cx, cy + r + 22), label, font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")


class AnalogClock(Screen):
    """Ж — аналог: стрелочный циферблат слева, данные колонкой справа."""

    name = "clock_analog"
    title = "Ж - аналоговые стрелки"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        cx, cy, r = 150, 158, 118
        hour_angle = (state.now.hour % 12) * 30 + state.now.minute * 0.5
        minute_angle = state.now.minute * 6

        def paint(d: ImageDraw.ImageDraw, s: int) -> None:
            c = r + 6
            d.ellipse(((c - r) * s, (c - r) * s, (c + r) * s, (c + r) * s),
                      outline=theme.LINE, width=3 * s)
            for tick in range(12):
                major = tick % 3 == 0
                outer = polar(c * s, c * s, tick * 30, (r - 4) * s)
                inner = polar(c * s, c * s, tick * 30, (r - (18 if major else 11)) * s)
                d.line((*inner, *outer), fill=theme.FG if major else theme.DIM,
                       width=(4 if major else 2) * s)
            d.line((c * s, c * s, *polar(c * s, c * s, hour_angle, r * s * 0.52)),
                   fill=theme.FG, width=8 * s)
            d.line((c * s, c * s, *polar(c * s, c * s, minute_angle, r * s * 0.80)),
                   fill=theme.FG, width=5 * s)
            d.ellipse(((c - 6) * s, (c - 6) * s, (c + 6) * s, (c + 6) * s), fill=theme.ACCENT)

        face = smooth(((r + 6) * 2, (r + 6) * 2), paint)
        frame.paste(face, (cx - r - 6, cy - r - 6), face)
        draw.text((cx, h - 26), _date(state), font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")

        right = 296
        label, color = _status(state)
        draw.ellipse((right, 27, right + 10, 37), fill=color)
        draw.text((right + 20, 32), label, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")
        for i, (value, caption, cell_color) in enumerate(_cells(state)):
            y = 96 + i * 74
            draw.text((right, y), value, font=theme.font(32, bold=True), fill=cell_color, anchor="lm")
            draw.text((right, y + 25), caption, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")


class GaugeClock(Screen):
    """З — приборная панель: CO2 стрелочным индикатором, время цифрами.

    Тот же язык, что просит Режим 5 — ориентир AIDA64 SensorPanel (п.10).
    """

    name = "clock_gauge"
    title = "З - приборная панель"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        env, out = state.env, state.weather
        frac = _frac(env.co2, 400, 2000)
        color = theme.co2_color(env.co2)
        cx, cy, r = 118, 168, 96

        def paint(d: ImageDraw.ImageDraw, s: int) -> None:
            c = r + 10
            box = ((c - r) * s, (c - r) * s, (c + r) * s, (c + r) * s)
            d.arc(box, 150, 390, fill=theme.LINE, width=13 * s)
            d.arc(box, 150, 150 + 240 * frac, fill=color, width=13 * s)
            for i in range(5):
                a = 150 + 60 * i + 90
                d.line((*polar(c * s, c * s, a, (r - 24) * s), *polar(c * s, c * s, a, (r - 34) * s)),
                       fill=theme.DIM, width=2 * s)
            needle = 150 + 240 * frac + 90
            d.line((c * s, c * s, *polar(c * s, c * s, needle, (r - 28) * s)), fill=theme.FG, width=4 * s)
            d.ellipse(((c - 7) * s, (c - 7) * s, (c + 7) * s, (c + 7) * s), fill=theme.FG)

        gauge = smooth(((r + 10) * 2, (r + 10) * 2), paint)
        frame.paste(gauge, (cx - r - 10, cy - r - 10), gauge)
        draw.text((cx, cy + 46), str(env.co2) if env.co2 is not None else "--",
                  font=theme.font(38, bold=True), fill=color, anchor="mm")
        draw.text((cx, cy + 78), "CO2, ppm", font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")

        right = 254
        label, status_color = _status(state)
        draw.ellipse((right, 27, right + 10, 37), fill=status_color)
        draw.text((right + 20, 32), label, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")

        draw.text((right, 104), state.now.strftime("%H:%M"),
                  font=theme.font(82, bold=True), fill=theme.FG, anchor="lm")
        # Условие погоды уводим к дате: в строке «на улице» оно упиралось
        # в подпись слева, потому что значение выравнено по правому краю.
        subtitle = _date(state) + (f"  ·  {out.cond}" if out.cond else "")
        draw.text((right, 156), subtitle, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")
        draw.line((right, 182, w - theme.PAD, 182), fill=theme.LINE)

        rows = [
            ("на улице", f"{out.temp:+.0f}°" if out.temp is not None else "--"),
            ("в комнате", f"{env.temperature:.1f}°" if env.temperature is not None else "--"),
            ("влажность", f"{env.humidity:.0f}%" if env.humidity is not None else "--"),
        ]
        for i, (name, value) in enumerate(rows):
            y = 208 + i * 34
            draw.text((right, y), name, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")
            draw.text((w - theme.PAD, y), value, font=theme.font(theme.BODY, bold=True),
                      fill=theme.FG, anchor="rm")
