"""Значки погоды, нарисованные примитивами.

Не шрифт и не картинки. Шрифт отпадает потому, что эмодзи погоды в Nunito
нет — Pillow молча подставит пустой квадратик, и заметить это можно будет
только на живом экране (так уже было с нотой). Картинки отпадают потому,
что их пришлось бы держать в нескольких размерах: значок нужен и крупным
на экране погоды, и мелким на карточке часов.

Примитивы решают оба: рисуются в любом размере, зависимостей ноль.

Все значки строятся от единичного квадрата: параметр size задаёт сторону,
внутренняя разметка считается от неё. Поэтому один и тот же вызов даёт
и значок 22 px на карточке, и 96 px на подробном экране.
"""

from __future__ import annotations

from PIL import ImageDraw

from .. import theme

#: Семейства по кодам WMO. Внутри семейства значок один: отличать морось
#: от мороси на экране 480x320 бессмысленно, а словом это и так сказано.
FAMILIES = {
    "clear": (0,),
    "partly": (1, 2),
    "overcast": (3,),
    "fog": (45, 48),
    "drizzle": (51, 53, 55, 56, 57),
    "rain": (61, 63, 66, 67, 80, 81),
    "downpour": (65, 82),
    "snow": (71, 73, 75, 77, 85, 86),
    "thunder": (95, 96, 99),
}

_BY_CODE = {code: name for name, codes in FAMILIES.items() for code in codes}

SUN = (250, 200, 90)
MOON = (198, 210, 235)
CLOUD = (176, 188, 208)
CLOUD_DARK = (128, 140, 160)
DROP = (120, 180, 255)
SNOW = (222, 236, 255)
BOLT = (250, 200, 90)


def family(code: int | None) -> str:
    """Какое семейство значков отвечает коду погоды.

    Проверка на None отдельная, а не через `code or ...`: ясная погода —
    это код ноль, а ноль в Python ложный, и «ясно» превращалось бы в
    «пасмурно». Та же ловушка, что съела поправку датчика воздуха.
    """
    if code is None:
        return "overcast"
    return _BY_CODE.get(code, "overcast")


def _sun(draw: ImageDraw.ImageDraw, x: float, y: float, size: float,
         colour=SUN) -> None:
    """Солнце: круг и восемь лучей."""
    radius = size * 0.26
    cx, cy = x + size / 2, y + size / 2
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)
    ray, gap = size * 0.16, size * 0.08
    width = max(2, round(size * 0.055))
    for index in range(8):
        # Диагональные лучи короче прямых — так у солнца остаётся круглый
        # силуэт, а не квадратный.
        angle = index * 45
        length = ray if angle % 90 == 0 else ray * 0.78
        dx, dy = _direction(angle)
        start = radius + gap
        draw.line((cx + dx * start, cy + dy * start,
                   cx + dx * (start + length), cy + dy * (start + length)),
                  fill=colour, width=width)


def _direction(angle: int) -> tuple[float, float]:
    """Единичный вектор для угла, кратного 45 градусам."""
    diagonal = 0.7071
    return {
        0: (1.0, 0.0), 45: (diagonal, diagonal), 90: (0.0, 1.0),
        135: (-diagonal, diagonal), 180: (-1.0, 0.0),
        225: (-diagonal, -diagonal), 270: (0.0, -1.0), 315: (diagonal, -diagonal),
    }[angle]


def _moon(draw: ImageDraw.ImageDraw, x: float, y: float, size: float,
          back, bite_below: bool = False) -> None:
    """Месяц: круг, из которого вырезан второй круг цветом фона.

    Вырезаем перекрытием, а не маской: фон карточки сплошной, и результат
    тот же, зато без второго изображения и композиции поверх.

    bite_below разворачивает вырез вниз, оставляя видимой верхнюю дугу.
    Нужно для «малооблачно ночью»: там низ месяца закрывает облако, и при
    обычном вырезе от него остаётся невнятная запятая — видимое и
    вырезанное съедают друг друга.
    """
    radius = size * 0.3
    cx, cy = x + size * 0.52, y + size / 2
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=MOON)
    bite = radius * 0.92
    lift = radius * (0.34 if bite_below else -0.30)
    bx, by = cx - radius * 0.52, cy + lift
    draw.ellipse((bx - bite, by - bite, bx + bite, by + bite), fill=back)


def _cloud(draw: ImageDraw.ImageDraw, x: float, y: float, size: float,
           colour=CLOUD, scale: float = 1.0) -> tuple[float, float]:
    """Облако из трёх кругов и основания. Возвращает низ и левый край."""
    width = size * 0.82 * scale
    left = x + (size - width) / 2
    top = y + size * (0.30 if scale >= 1 else 0.34)
    bottom = top + width * 0.42

    big = width * 0.30
    draw.ellipse((left + width * 0.16, top, left + width * 0.16 + big * 2,
                  top + big * 2), fill=colour)
    small = width * 0.21
    draw.ellipse((left, top + big * 0.7, left + small * 2,
                  top + big * 0.7 + small * 2), fill=colour)
    draw.ellipse((left + width - small * 2, top + big * 0.55,
                  left + width, top + big * 0.55 + small * 2), fill=colour)
    draw.rounded_rectangle((left, bottom - width * 0.22, left + width, bottom),
                           radius=width * 0.11, fill=colour)
    return bottom, left


def _drops(draw: ImageDraw.ImageDraw, left: float, top: float, width: float,
           count: int, colour=DROP, length: float = 1.0) -> None:
    """Косые штрихи под облаком — дождь."""
    thickness = max(2, round(width * 0.05))
    step = width / (count + 1)
    drop = width * 0.17 * length
    for index in range(count):
        sx = left + step * (index + 1)
        draw.line((sx + drop * 0.35, top, sx - drop * 0.35, top + drop),
                  fill=colour, width=thickness)


def _flakes(draw: ImageDraw.ImageDraw, left: float, top: float, width: float,
            count: int) -> None:
    """Снежинки — три перекрещенных штриха."""
    arm = width * 0.065
    thickness = max(1, round(width * 0.035))
    step = width / (count + 1)
    for index in range(count):
        cx = left + step * (index + 1)
        cy = top + arm + (width * 0.06 if index % 2 else 0)
        for angle in (90, 30, 150):
            dx, dy = _arm(angle)
            draw.line((cx - dx * arm, cy - dy * arm, cx + dx * arm, cy + dy * arm),
                      fill=SNOW, width=thickness)


def _arm(angle: int) -> tuple[float, float]:
    return {90: (0.0, 1.0), 30: (0.866, 0.5), 150: (-0.866, 0.5)}[angle]


def _bolt(draw: ImageDraw.ImageDraw, left: float, top: float, width: float) -> None:
    """Молния — ломаная в виде зигзага."""
    unit = width * 0.16
    cx = left + width / 2
    draw.polygon([
        (cx + unit * 0.5, top),
        (cx - unit * 0.7, top + unit * 1.25),
        (cx - unit * 0.05, top + unit * 1.25),
        (cx - unit * 0.8, top + unit * 2.6),
        (cx + unit * 0.9, top + unit * 0.95),
        (cx + unit * 0.15, top + unit * 0.95),
    ], fill=BOLT)


def draw_icon(draw: ImageDraw.ImageDraw, x: float, y: float, size: float,
              code: int | None, day: bool = True, back=theme.SURFACE) -> None:
    """Нарисовать значок погоды в квадрате size x size с углом в (x, y).

    back — цвет подложки: им вырезается месяц, поэтому он должен совпадать
    с тем, на чём значок лежит.
    """
    kind = family(code)

    if kind == "clear":
        (_sun if day else lambda d, a, b, c: _moon(d, a, b, c, back))(draw, x, y, size)
        return

    if kind == "partly":
        # Светило выглядывает из-за облака: сдвигаем его вверх и вправо,
        # облако рисуем поверх — порядок и создаёт перекрытие.
        if day:
            _sun(draw, x + size * 0.20, y - size * 0.13, size * 0.72)
        else:
            _moon(draw, x + size * 0.26, y - size * 0.20, size * 0.70, back,
                  bite_below=True)
        _cloud(draw, x, y + size * 0.10, size, scale=0.92)
        return

    if kind == "fog":
        bottom, left = _cloud(draw, x, y - size * 0.06, size, colour=CLOUD_DARK)
        width = size * 0.82
        thickness = max(2, round(size * 0.045))
        for index in range(3):
            inset = width * (0.06 if index % 2 else 0.16)
            row = bottom + size * (0.07 + index * 0.10)
            draw.line((left + inset, row, left + width - inset, row),
                      fill=CLOUD, width=thickness)
        return

    bottom, left = _cloud(draw, x, y - size * 0.04, size,
                          colour=CLOUD_DARK if kind in ("downpour", "thunder") else CLOUD)
    width = size * 0.82
    below = bottom + size * 0.06

    if kind == "drizzle":
        _drops(draw, left, below, width, 3, length=0.6)
    elif kind == "rain":
        _drops(draw, left, below, width, 3)
    elif kind == "downpour":
        _drops(draw, left, below, width, 5, length=1.25)
    elif kind == "snow":
        _flakes(draw, left, below, width, 4)
    elif kind == "thunder":
        _bolt(draw, left, below - size * 0.02, width)
