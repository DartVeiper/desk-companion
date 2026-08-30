"""Помощники отрисовки.

Pillow не сглаживает дуги и окружности — края выходят лесенкой. Рисуем
на увеличенном холсте и ужимаем обратно. Для Режима 1 это неважно (кадр
меняется раз в минуту), а вот Режиму 5 с круглым индикатором AIDA64
понадобится: там стоит супersample'ить только сам индикатор, а не кадр.
"""

from __future__ import annotations

import math
from typing import Callable

from PIL import Image, ImageDraw, ImageFont


def smooth(
    size: tuple[int, int],
    painter: Callable[[ImageDraw.ImageDraw, int], None],
    scale: int = 3,
) -> Image.Image:
    """Отрисовать painter на холсте в scale раз крупнее и ужать до size."""
    big = Image.new("RGBA", (size[0] * scale, size[1] * scale), (0, 0, 0, 0))
    painter(ImageDraw.Draw(big), scale)
    return big.resize(size, Image.LANCZOS)


def polar(cx: float, cy: float, angle_deg: float, radius: float) -> tuple[float, float]:
    """Точка на окружности. 0 градусов — вверх, дальше по часовой."""
    a = math.radians(angle_deg - 90)
    return cx + radius * math.cos(a), cy + radius * math.sin(a)


def ring(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    fraction: float,
    width: float,
    color: tuple[int, int, int],
    track: tuple[int, int, int],
) -> None:
    """Кольцо прогресса: серый след плюс заполненная дуга от 12 часов."""
    draw.arc(box, 0, 360, fill=track, width=int(width))
    if fraction > 0:
        draw.arc(box, -90, -90 + 360 * min(fraction, 1.0), fill=color, width=int(width))


def hand(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    angle_deg: float,
    length: float,
    width: float,
    color: tuple[int, int, int],
    tail: float = 0.0,
) -> None:
    """Стрелка с закруглёнными концами.

    Pillow не умеет round cap у линий, поэтому концы закрываем кругами —
    без этого стрелка выглядит обрубленной, и весь циферблат дешевеет.
    """
    tip = polar(cx, cy, angle_deg, length)
    back = polar(cx, cy, angle_deg + 180, tail)
    draw.line((*back, *tip), fill=color, width=int(width))
    r = width / 2
    draw.ellipse((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), fill=color)
    if tail:
        draw.ellipse((back[0] - r, back[1] - r, back[0] + r, back[1] + r), fill=color)


def clock_face(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    scale: int,
    bright: tuple[int, int, int],
    dim: tuple[int, int, int],
    numeral_font,
    numeral_radius: float = 0.80,
) -> None:
    """Разметка циферблата: минутные штрихи, часовые бруски, цифры 12/3/6/9.

    Радиусы разные по осям — штрихи ложатся на эллипс во весь экран, поэтому
    в углах они длиннее. Отсюда «лучи», а не аккуратный кружок посередине.
    """
    numerals = {0: "12", 15: "3", 30: "6", 45: "9"}

    for i in range(60):
        angle = i * 6
        if i in numerals:
            continue
        ux, uy = polar(cx, cy, angle, 1.0)
        ox, oy = cx + (ux - cx) * rx, cy + (uy - cy) * ry
        major = i % 5 == 0
        k = 0.78 if major else 0.90
        draw.line(
            (cx + (ox - cx) * k, cy + (oy - cy) * k, ox, oy),
            fill=bright if major else dim,
            width=int((6 if major else 3) * scale),
        )

    for i, text in numerals.items():
        ux, uy = polar(cx, cy, i * 6, 1.0)
        draw.text(
            (cx + (ux - cx) * rx * numeral_radius, cy + (uy - cy) * ry * numeral_radius),
            text, font=numeral_font, fill=bright, anchor="mm",
        )


def dot_matrix(
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    cell: int = 6,
    dot_ratio: float = 0.82,
    max_width: int | None = None,
) -> Image.Image:
    """Текст точечной матрицей, как на светодиодном табло.

    Рендерим мелко, потом каждый непустой пиксель превращаем в точку. На
    480x320 это выигрышнее фотореализма: пиксельная эстетика на такой
    плотности выглядит намеренной, а не следствием нехватки разрешения.

    Размер ячейки ужимается под max_width. Без этого смена шрифта ломает
    вёрстку: у одного и того же кегля ширина глифов разная, и надпись
    вылезает за экран.
    """
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    w, h = max(1, right - left), max(1, bottom - top)

    if max_width is not None:
        cell = max(2, min(cell, max_width // w))
    dot = max(1, round(cell * dot_ratio))

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-left, -top), text, font=font, fill=255)
    pixels = mask.load()

    out = Image.new("RGBA", (w * cell, h * cell), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    for y in range(h):
        for x in range(w):
            if pixels[x, y] > 96:
                px, py = x * cell, y * cell
                draw.ellipse((px, py, px + dot - 1, py + dot - 1), fill=color)
    return out
