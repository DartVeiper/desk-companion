"""Варианты оформления Режима 1 — черновики для выбора стиля.

Файл временный: когда стиль выбран, победитель переезжает в clock.py,
остальные удаляются. В config.toml не подключены.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..state import State
from .base import Screen
from .clock import _MONTHS, _WEEKDAYS


def _date(state: State) -> str:
    return f"{_WEEKDAYS[state.now.weekday()]}, {state.now.day} {_MONTHS[state.now.month - 1]}"


def _cells(state: State) -> list[tuple[str, str, tuple[int, int, int]]]:
    out = state.weather
    env = state.env
    return [
        (
            "--" if out.temp is None else f"{out.temp:+.0f}°",
            f"на улице  {out.cond}" if out.cond else "на улице",
            theme.DIM if out.temp is None else theme.FG,
        ),
        (
            "--" if env.co2 is None else str(env.co2),
            "CO2, ppm",
            theme.co2_color(env.co2),
        ),
        (
            "--" if env.temperature is None else f"{env.temperature:.1f}°",
            "в комнате" if env.humidity is None else f"в комнате  {env.humidity:.0f}%",
            theme.DIM if env.temperature is None else theme.FG,
        ),
    ]


def _status(state: State) -> tuple[str, tuple[int, int, int]]:
    if state.desk.manual_status:
        return state.desk.manual_status, theme.WARN
    return ("за столом", theme.OK) if state.desk.presence else ("никого", theme.DIM)


class CardsClock(Screen):
    """Б — плитки. Данные в карточках, как в дашборде."""

    name = "clock_cards"
    title = "Б - плитки"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        pad, gap = 14, 10

        draw.rounded_rectangle((pad, pad, w - pad, 178), radius=16, fill=theme.SURFACE)
        label, color = _status(state)
        draw.ellipse((pad + 18, 33, pad + 28, 43), fill=color)
        draw.text((pad + 36, 38), label, font=theme.font(theme.SMALL), fill=theme.DIM, anchor="lm")
        draw.text(
            (w // 2, 104), state.now.strftime("%H:%M"),
            font=theme.font(102, bold=True), fill=theme.FG, anchor="mm",
        )
        draw.text((w // 2, 156), _date(state), font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")

        cells = _cells(state)
        cw = (w - pad * 2 - gap * (len(cells) - 1)) / len(cells)
        for i, (value, caption, color) in enumerate(cells):
            x0 = pad + i * (cw + gap)
            draw.rounded_rectangle((x0, 190, x0 + cw, h - pad), radius=16, fill=theme.SURFACE)
            draw.text((x0 + cw / 2, 232), value, font=theme.font(36, bold=True), fill=color, anchor="mm")
            draw.text((x0 + cw / 2, 276), caption, font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")


class SplitClock(Screen):
    """В — асимметрия. Время слева, данные колонкой справа."""

    name = "clock_split"
    title = "В - асимметрия"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size
        divider = 302

        label, color = _status(state)
        draw.ellipse((theme.PAD, 33, theme.PAD + 10, 43), fill=color)
        draw.text((theme.PAD + 20, 38), label, font=theme.font(theme.SMALL), fill=theme.DIM, anchor="lm")

        draw.text(
            (theme.PAD, 150), state.now.strftime("%H:%M"),
            font=theme.font(96, bold=True), fill=theme.FG, anchor="lm",
        )
        draw.text((theme.PAD + 4, 214), _date(state), font=theme.font(theme.SMALL), fill=theme.DIM, anchor="lm")

        rain = state.weather.rain_soon_minutes
        if rain is not None:
            draw.text(
                (theme.PAD + 4, 248), f"дождь через {rain} мин",
                font=theme.font(theme.SMALL), fill=theme.ACCENT, anchor="lm",
            )

        draw.line((divider, 34, divider, h - 34), fill=theme.LINE)
        for i, (value, caption, color) in enumerate(_cells(state)):
            y = 74 + i * 86
            draw.text((divider + 26, y), value, font=theme.font(38, bold=True), fill=color, anchor="lm")
            draw.text((divider + 26, y + 30), caption, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")


class MinimalClock(Screen):
    """Г — минимал. Время во весь экран, данные одной строкой."""

    name = "clock_minimal"
    title = "Г - минимал"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        w, h = frame.size

        label, color = _status(state)
        draw.ellipse((theme.PAD, 27, theme.PAD + 9, 36), fill=color)
        draw.text((theme.PAD + 19, 32), label, font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")

        draw.text(
            (w // 2, 148), state.now.strftime("%H:%M"),
            font=theme.font(158, bold=True), fill=theme.FG, anchor="mm",
        )
        draw.text((w // 2, 238), _date(state), font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")

        env, out = state.env, state.weather
        parts = []
        if out.temp is not None:
            parts.append(f"улица {out.temp:+.0f}°")
        if env.co2 is not None:
            parts.append(f"CO2 {env.co2}")
        if env.temperature is not None:
            parts.append(f"дом {env.temperature:.1f}°")
        line = "   ·   ".join(parts) if parts else "нет данных"
        draw.text(
            (w // 2, 288), line,
            font=theme.font(theme.SMALL), fill=theme.co2_color(env.co2), anchor="mm",
        )
