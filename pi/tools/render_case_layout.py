"""Компоновка корпуса в разрезе: что где стоит и чем держится.

    py tools/render_case_layout.py

Текстом это уже описано в КОРПУС.md, но разрез отвечает на вопросы, до
которых текст доходит абзацами: почему датчик воздуха внизу, куда смотрит
радар, откуда и куда идёт воздух, сколько места съедают провода.

Рисуется вид сбоку — именно он показывает главное. Спереди корпус
выглядит просто прямоугольником с экраном.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme  # noqa: E402

# Картинка идёт в документацию, поэтому лежит в docs/, а не в preview.
OUT = Path(__file__).resolve().parents[2] / "docs" / "корпус-компоновка.png"

BG = (12, 12, 14)
INK = (238, 238, 240)
DIM = (120, 126, 136)
LINE = (58, 63, 72)
WALL = (92, 100, 114)
AIR = (120, 180, 255)
HOT = (238, 130, 110)
BEAM = (250, 200, 90)
PART = (32, 37, 46)

WIDTH, HEIGHT = 1520, 980
SCALE = 4.2  # пикселей на миллиметр

#: Ориентировочные габариты в миллиметрах. Настоящие приедут с обмеров —
#: здесь они нужны только чтобы пропорции на рисунке были честными.
CASE_D, CASE_H = 95, 120     # глубина и высота корпуса
TILT = 15                    # наклон назад, градусов


def mm(value: float) -> float:
    return value * SCALE


def part(draw, box, label, sub="", colour=PART, text=INK):
    """Прямоугольник детали с подписью внутри."""
    draw.rounded_rectangle(box, 5, fill=colour, outline=LINE, width=2)
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    draw.text((cx, cy - (7 if sub else 0)), label, font=theme.font(15, bold=True),
              fill=text, anchor="mm")
    if sub:
        draw.text((cx, cy + 10), sub, font=theme.font(12), fill=DIM, anchor="mm")


def arrow(draw, x0, y0, x1, y1, colour, width=3, head=9):
    """Стрелка потока."""
    draw.line((x0, y0, x1, y1), fill=colour, width=width)
    dx, dy = x1 - x0, y1 - y0
    length = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    draw.polygon([
        (x1, y1),
        (x1 - ux * head + px * head * 0.55, y1 - uy * head + py * head * 0.55),
        (x1 - ux * head - px * head * 0.55, y1 - uy * head - py * head * 0.55),
    ], fill=colour)


def callout(draw, x, y, number, colour=AIR):
    draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=BG, outline=colour, width=2)
    draw.text((x, y), str(number), font=theme.font(15, bold=True),
              fill=colour, anchor="mm")


NOTES = [
    (1, "Экран", AIR,
     "Наклон назад 15°. С кресла на вертикальный экран смотришь снизу, а у\n"
     "дешёвых матриц верхние углы обзора плохие — картинка бледнеет.\n"
     "Держится рамкой спереди и двумя рёбрами сзади: тач резистивный, на\n"
     "него давят, и без опоры стекло прогибается — точка касания уезжает."),
    (2, "Pi Zero 2 W", AIR,
     "На четырёх стойках M2.5, отверстия с шагом 58 × 23 мм по 3,5 мм от\n"
     "краёв. Стойки печатные или латунные — но не металлическая пластина\n"
     "во всю спину: она окажется прямо за радаром."),
    (3, "Радар LD2410", BEAM,
     "СКВОЗЬ ЭКРАН НЕ ВИДИТ: у матрицы металлический отражатель подсветки,\n"
     "он глушит 24 ГГц. Поэтому у радара своя полоса лица под экраном.\n"
     "Смотрит вперёд и чуть вверх, примерно на 10° — ты сидишь выше блока.\n"
     "Перед ним стенка 2–3 мм и зазор 5–10 мм: пластик вплотную расстраивает\n"
     "антенну. В конусе ±60° не должно быть ни винта, ни фольги, ни проводов.\n"
     "Держится двумя каплями горячего клея по углам, не винтами."),
    (4, "Датчик воздуха", HOT,
     "В самом низу и как можно дальше от Pi — тепло идёт вверх, низ всегда\n"
     "холоднее. Отверстия впуска прямо напротив кристалла (обмер P).\n"
     "Между ним и Pi — перегородка: излучение в упор греет сильнее конвекции."),
    (5, "Энкодер", AIR,
     "Справа, на высоте, куда ложится рука. Держится своей же гайкой, но\n"
     "стенка в этом месте должна быть тоньше высоты втулки (обмер J,\n"
     "обычно 5–7 мм), иначе резьбы не хватит."),
    (6, "Провода", DIM,
     "20–25 мм за платой, и это не запас, а измеренная величина (обмер Q):\n"
     "самодельные тройники со скрутками толще самих плат. Прижимать\n"
     "крышкой нельзя — место скрутки самое хрупкое во всей сборке."),
    (7, "Питание", DIM,
     "Выход снизу-сзади, с проточкой: натяжение должно идти на корпус, а не\n"
     "на разъём microUSB — он держится тремя пятаками и отрывается первым."),
]


def main() -> None:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)

    draw.text((40, 34), "Корпус в разрезе, вид сбоку",
              font=theme.font(32, bold=True), fill=INK)
    draw.text((40, 76),
              "Пропорции честные, размеры — ориентировочные: настоящие "
              "приедут с обмеров. Слева лицо, справа спина.",
              font=theme.font(16), fill=DIM)

    # --------------------------------------------------------- контур корпуса
    # Рисуем корпус прямо, а наклон показываем клином под ним. Наклонённый
    # разрез читается хуже: детали в нём приходится тоже наклонять, и
    # чертёж превращается в кашу, ничего не добавляя к пониманию.
    left, bottom = 230, 760
    depth, height = mm(CASE_D), mm(CASE_H)
    right, top = left + depth, bottom - height
    draw.rectangle((left, top, right, bottom), outline=WALL, width=4)

    def place(x_mm, y_mm, w_mm, h_mm):
        """Прямоугольник детали: x от лица, y от дна."""
        return (left + mm(x_mm), bottom - mm(y_mm + h_mm),
                left + mm(x_mm + w_mm), bottom - mm(y_mm))

    # 1 — экран, во всю переднюю стенку, кроме нижней полосы
    part(draw, place(2, 26, 9, 88), "экран", "и тач")
    # 3 — радар в нижней полосе лица, у него своё окно
    part(draw, place(2, 8, 9, 15), "радар", colour=(44, 38, 24))
    # 2 — Pi за экраном
    part(draw, place(17, 46, 34, 16), "Pi Zero 2 W")
    # 4 — датчик воздуха внизу, в стороне от радара
    part(draw, place(17, 6, 24, 12), "SCD41", "воздух")
    # перегородка между датчиком и Pi
    y = bottom - mm(30)
    draw.line((left + mm(14), y, left + mm(48), y), fill=WALL, width=3)
    draw.text((left + mm(31), y - 13), "перегородка", font=theme.font(12),
              fill=DIM, anchor="mm")
    # 5 — энкодер: на этом виде он за боковой стенкой
    box = place(17, 74, 16, 12)
    draw.rounded_rectangle(box, 5, outline=LINE, width=2)
    draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "энкодер",
              font=theme.font(12), fill=DIM, anchor="mm")
    # 6 — клубок проводов вдоль спины
    box = place(58, 14, 34, 86)
    draw.rounded_rectangle(box, 6, outline=DIM, width=2)
    draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "провода\n20-25 мм",
              font=theme.font(13), fill=DIM, anchor="mm", align="center")

    # ------------------------------------------------------------- воздух
    # Впуск в дне под датчиком, выпуск в крышке у спины. Одно отверстие не
    # проветривает: воздуху нужны вход и выход на разной высоте.
    for i in range(6):
        x = left + mm(18) + i * 14
        draw.line((x, bottom - 3, x, bottom + 10), fill=AIR, width=3)
    arrow(draw, left + mm(28), bottom + 40, left + mm(28), bottom - mm(4), AIR)
    draw.text((left + mm(28), bottom + 56), "впуск в дне", font=theme.font(14),
              fill=AIR, anchor="mm")

    for i in range(5):
        x = left + mm(62) + i * 14
        draw.line((x, top - 10, x, top + 3), fill=HOT, width=3)
    arrow(draw, left + mm(72), top + mm(14), left + mm(72), top - 30, HOT)
    draw.text((left + mm(72), top - 48), "выпуск в крышке",
              font=theme.font(14), fill=HOT, anchor="mm")

    # Путь воздуха внутри: от дна мимо Pi наверх и назад.
    arrow(draw, left + mm(30), bottom - mm(20), left + mm(38), bottom - mm(40), AIR, 2, 7)
    arrow(draw, left + mm(44), bottom - mm(66), left + mm(62), bottom - mm(100), HOT, 2, 7)

    # --------------------------------------------------------- конус радара
    import math
    rx, ry = left + mm(2), bottom - mm(15)
    for angle in (-50, 10, 60):
        rad = math.radians(angle)
        draw.line((rx, ry, rx - math.cos(rad) * 96, ry - math.sin(rad) * 96),
                  fill=BEAM, width=2)
    draw.text((rx - 92, ry - 104), "конус ±60°,\nвверх на 10°",
              font=theme.font(13), fill=BEAM, anchor="mm", align="center")

    # ------------------------------------------------------------ наклон
    wedge = [(left, bottom + 4), (right, bottom + 4),
             (right, bottom + 4 + (right - left) * TILT / 100)]
    draw.polygon(wedge, outline=DIM, width=2)
    draw.text(((left + right) / 2, bottom + 92), f"клин {TILT}°: экран смотрит в лицо",
              font=theme.font(15), fill=DIM, anchor="mm")

    # ------------------------------------------------------------- выноски
    spots = {1: place(2, 26, 9, 88), 2: place(17, 46, 34, 16),
             3: place(2, 8, 9, 15), 4: place(17, 6, 24, 12),
             5: place(17, 74, 16, 12), 6: place(58, 14, 34, 86)}
    for number, box in spots.items():
        colour = BEAM if number == 3 else HOT if number == 4 else AIR
        side = box[0] - 18 if number in (1, 3) else box[2] + 18
        callout(draw, side, (box[1] + box[3]) / 2, number, colour)
    callout(draw, right + 18, bottom - mm(6), 7, DIM)

    # ------------------------------------------------------- правая колонка
    x = 680
    y = 128
    for number, title, colour, text in NOTES:
        callout(draw, x + 16, y + 12, number, colour)
        draw.text((x + 46, y + 2), title, font=theme.font(19, bold=True), fill=INK)
        draw.multiline_text((x + 46, y + 28), text, font=theme.font(14),
                            fill=DIM, spacing=6)
        y += 30 + text.count("\n") * 21 + 34

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    print(f"\n  {OUT}\n  {canvas.width}x{canvas.height}\n")


if __name__ == "__main__":
    main()
