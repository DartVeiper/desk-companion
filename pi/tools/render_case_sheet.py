"""Карточка обмеров под корпус.

    py tools/render_case_sheet.py

Зачем. Корпус нельзя нарисовать по описанию модуля из магазина: у «экрана
4 дюйма» плата бывает от 105 до 112 мм длиной, а стекло стоит на ней не по
центру. Ошибка в полтора миллиметра — и рамка наезжает на изображение либо
болтается щелью. Печать при этом занимает часы, а заметить промах можно
только после неё.

Поэтому сначала обмер. Здесь перечислено ровно то, что нужно, и сказано,
на что каждое число влияет, — чтобы понятно было, где можно округлить, а
где нельзя.

Чем мерить. Штангенциркуль лучше всего, но линейки хватит: критичных
размеров четыре, остальные с запасом в миллиметр.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme  # noqa: E402

# Картинка идёт в документацию, поэтому лежит в docs/, а не в preview:
# preview — свалка побочных отрисовок и в репозиторий не попадает.
OUT = Path(__file__).resolve().parents[2] / "docs" / "корпус-обмеры.png"

BG = (12, 12, 14)
INK = (238, 238, 240)
DIM = (120, 126, 136)
LINE = (58, 63, 72)
FIELD = (26, 29, 35)
ACCENT = (120, 180, 255)
WARN = (235, 190, 90)

WIDTH, MARGIN = 1500, 40
PANEL_GAP = 18

#: (модуль, примечание, [(метка, что мерить, зачем, критично)])
BLOCKS = [
    ("ЭКРАН ST7796S", "плату вынимать не надо, мерить прямо на месте", [
        ("A", "длина платы", "внутренняя ширина корпуса", True),
        ("B", "ширина платы", "внутренняя высота корпуса", True),
        ("C", "видимое стекло: ширина", "вырез в передней рамке", True),
        ("D", "видимое стекло: высота", "вырез в передней рамке", True),
        ("E", "от края платы до стекла слева", "стекло стоит не по центру", True),
        ("F", "от края платы до стекла сверху", "то же самое по вертикали", True),
        ("G", "толщина платы со стеклом", "глубина посадочного места", False),
        ("H", "насколько торчит гребёнка сзади", "запас по глубине", False),
    ]),
    ("ЭНКОДЕР KY-040", "торчит наружу, поэтому мерить надо резьбу", [
        ("I", "диаметр резьбовой втулки", "отверстие в стенке", True),
        ("J", "высота втулки над платой", "толщина стенки не больше этого", True),
        ("K", "размеры платы, Д x Ш", "место внутри", False),
        ("L", "длина вала над втулкой", "хватит ли на ручку", False),
    ]),
    ("РАДАР LD2410", "смотрит в переднюю стенку, металла перед ним быть не должно", [
        ("M", "размеры платы, Д x Ш", "место внутри", False),
        ("N", "с какой стороны антенна", "этой стороной к человеку", False),
    ]),
    ("ДАТЧИК ВОЗДУХА SCD41", "ему нужен продув, см. КОРПУС.md", [
        ("O", "размеры платы, Д x Ш", "место внизу, у впускных отверстий", False),
        ("P", "где на плате сам датчик", "напротив него — отверстия", False),
    ]),
    ("СБОРКА ЦЕЛИКОМ", "то, из-за чего первый корпус обычно выходит мелким", [
        ("Q", "толщина клубка проводов за платой", "глубина корпуса", True),
        ("R", "высота платы Pi с надетыми проводами", "то же", False),
    ]),
]

KNOWN = [
    "Pi Zero 2 W — 65 x 30 x 5 мм, отверстия M2.5 с шагом 58 x 23 мм,",
    "по 3,5 мм от каждого края. Это стандарт, мерить не нужно.",
]


def field(draw: ImageDraw.ImageDraw, x: float, y: float, w: float = 118,
          h: float = 30) -> None:
    """Пустая клетка под число."""
    draw.rounded_rectangle((x, y, x + w, y + h), 6, fill=FIELD, outline=LINE)
    draw.text((x + w - 12, y + h / 2), "мм", font=theme.font(14),
              fill=DIM, anchor="rm")


def panel(draw: ImageDraw.ImageDraw, x: float, y: float, w: float,
          title: str, note: str, rows: list) -> float:
    """Нарисовать блок модуля. Возвращает его нижнюю границу."""
    height = 74 + len(rows) * 38 + 14
    draw.rounded_rectangle((x, y, x + w, y + height), 12, outline=LINE, width=2)
    draw.text((x + 22, y + 22), title, font=theme.font(20, bold=True), fill=INK)
    draw.text((x + 22, y + 48), note, font=theme.font(15), fill=DIM)

    row_y = y + 78
    for label, what, why, critical in rows:
        colour = WARN if critical else ACCENT
        draw.ellipse((x + 22, row_y + 4, x + 46, row_y + 28), outline=colour, width=2)
        draw.text((x + 34, row_y + 16), label, font=theme.font(15, bold=True),
                  fill=colour, anchor="mm")
        draw.text((x + 60, row_y + 8), what, font=theme.font(17), fill=INK)
        draw.text((x + 60, row_y + 27), why, font=theme.font(14), fill=DIM)
        field(draw, x + w - 142, row_y + 1)
        row_y += 38

    return y + height


def main() -> None:
    heights = [74 + len(rows) * 38 + 14 + PANEL_GAP for _, _, rows in
               [(t, n, r) for t, n, r in BLOCKS]]
    column = (WIDTH - 2 * MARGIN - 30) / 2
    # Раскладываем по двум колонкам, набирая левую до половины общей высоты.
    total = sum(heights)
    left, right, taken = [], [], 0.0
    for block, h in zip(BLOCKS, heights):
        (left if taken < total / 2 else right).append(block)
        taken += h

    def column_height(blocks) -> float:
        return sum(74 + len(r) * 38 + 14 + PANEL_GAP for _, _, r in blocks)

    height = int(max(column_height(left), column_height(right))) + 250
    canvas = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(canvas)

    draw.text((MARGIN, 34), "Обмеры под корпус", font=theme.font(34, bold=True),
              fill=INK)
    draw.text((MARGIN, 78),
              "Заполни клетки — по ним соберётся модель. "
              "Жёлтым отмечено то, где ошибка в миллиметр уже видна.",
              font=theme.font(17), fill=DIM)

    y_left = y_right = 124
    for block in left:
        y_left = panel(draw, MARGIN, y_left, column, *block) + PANEL_GAP
    for block in right:
        y_right = panel(draw, MARGIN + column + 30, y_right, column, *block) + PANEL_GAP

    bottom = max(y_left, y_right) + 6
    draw.line((MARGIN, bottom, WIDTH - MARGIN, bottom), fill=LINE, width=2)
    for i, line in enumerate(KNOWN):
        draw.text((MARGIN, bottom + 18 + i * 24), line, font=theme.font(16), fill=DIM)

    draw.text((MARGIN, bottom + 78),
              "Что мерить нечем — пропусти: в модели у всего есть значения "
              "по умолчанию, и без числа она соберётся по ним.",
              font=theme.font(16), fill=ACCENT)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    print(f"\n  {OUT}")
    print(f"  {canvas.width}x{canvas.height}, блоков {len(BLOCKS)}, "
          f"размеров {sum(len(r) for _, _, r in BLOCKS)}\n")


if __name__ == "__main__":
    main()
