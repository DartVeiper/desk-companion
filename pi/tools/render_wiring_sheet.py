"""Маршрутный лист: что в какую ножку втыкать, по порядку.

    py tools/render_wiring_sheet.py

Первая версия рисовала линии от каждого модуля к гребёнке — и утонула: у
одного экрана тринадцать проводов близких оттенков, а модулей четыре.
Линии хороши, пока их около десятка на один модуль (см.
render_display_wiring.py), и бесполезны, когда их двадцать шесть: рядом с
гребёнкой они сливаются в кашу, и разобрать, какая куда, нельзя.

Поэтому здесь не схема, а лист для работы руками: строки отсортированы по
номеру ножки Pi, у каждой галочка. Идёшь по гребёнке сверху вниз, втыкаешь,
вычёркиваешь. Справа та же гребёнка картинкой — чтобы видеть, где ты.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme

OUT = Path(__file__).resolve().parents[1] / "preview" / "маршрутный-лист.png"

BG = (12, 12, 14)
INK = (238, 238, 240)
DIM = (120, 126, 136)
FREE = (40, 44, 52)
NOTE = (235, 190, 90)

DISPLAY = (64, 150, 240)
TOUCH = (168, 150, 244)
ENCODER = (226, 168, 60)
RADAR = (232, 110, 64)
AIR = (46, 178, 128)
POWER = (236, 104, 104)
GROUND = (140, 148, 160)

# (ножка Pi, модуль, ножка модуля, примечание, цвет)
WIRES = [
    (1, "энкодер", "+", "3,3 В · тройник", POWER),
    (1, "воздух SCD41", "VCC", "3,3 В · тот же тройник", POWER),
    (3, "воздух SCD41", "SDA", "GPIO2", AIR),
    (4, "радар LD2410", "VCC", "5 В — не 3,3!", POWER),
    (5, "воздух SCD41", "SCL", "GPIO3", AIR),
    (6, "экран", "GND", "общий", GROUND),
    (8, "радар LD2410", "RX", "GPIO14 — это TX платы", RADAR),
    (9, "энкодер", "GND", "общий", GROUND),
    (10, "радар LD2410", "TX", "GPIO15 — это RX платы", RADAR),
    (11, "энкодер", "CLK", "GPIO17", ENCODER),
    (12, "экран", "LED", "GPIO18 · подсветка", DISPLAY),
    (13, "энкодер", "DT", "GPIO27", ENCODER),
    (14, "воздух SCD41", "GND", "общий", GROUND),
    (15, "энкодер", "SW", "GPIO22 · кнопка", ENCODER),
    (17, "экран", "VCC", "3,3 В", POWER),
    (18, "экран", "RESET", "GPIO24", DISPLAY),
    (19, "экран", "SDI (MOSI)", "GPIO10 · тройник", DISPLAY),
    (19, "тач", "T_DIN", "тот же тройник", TOUCH),
    (20, "радар LD2410", "GND", "общий", GROUND),
    (21, "экран", "SDO (MISO)", "GPIO9 · тройник", DISPLAY),
    (21, "тач", "T_DO", "тот же тройник", TOUCH),
    (22, "экран", "DC / RS", "GPIO25", DISPLAY),
    (23, "экран", "SCK", "GPIO11 · тройник", DISPLAY),
    (23, "тач", "T_CLK", "тот же тройник", TOUCH),
    (24, "экран", "CS", "GPIO8 · CE0", DISPLAY),
    (26, "тач", "T_CS", "GPIO7 · CE1", TOUCH),
]

NOT_CONNECTED = [("тач", "T_IRQ"), ("радар LD2410", "OUT")]

SPLITTERS = [
    (1, "энкодер  +", "воздух  VCC", POWER),
    (19, "экран  SDI (MOSI)", "тач  T_DIN", DISPLAY),
    (21, "экран  SDO (MISO)", "тач  T_DO", DISPLAY),
    (23, "экран  SCK", "тач  T_CLK", DISPLAY),
]

LEGEND = [("экран ST7796S", DISPLAY), ("тач XPT2046", TOUCH),
          ("энкодер KY-040", ENCODER), ("радар LD2410", RADAR),
          ("датчик воздуха SCD41", AIR), ("питание", POWER), ("общий GND", GROUND)]

ROW = 42
PIN_ROW = 32
PAD = 40


def main() -> None:
    f_pin = theme.font(19, bold=True)
    f_mod = theme.font(18, bold=True)
    f_leg = theme.font(14)
    f_small = theme.font(13)
    f_head = theme.font(16, bold=True)
    f_title = theme.font(30, bold=True)
    f_sub = theme.font(15)

    per_col = (len(WIRES) + 1) // 2
    col_w = 480
    list_h = per_col * ROW
    pi_h = 20 * PIN_ROW

    width = PAD * 2 + col_w * 2 + 230
    top = PAD + 120
    body = max(list_h, pi_h)
    splitters_y = top + body + 50
    bh = 104 + len(SPLITTERS) * 34
    height = splitters_y + bh + 110

    canvas = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(canvas)

    d.text((PAD, PAD), "Desk Companion — маршрутный лист", font=f_title, fill=INK)
    d.text((PAD, PAD + 46),
           "По порядку ножек гребёнки, сверху вниз. Воткнул — вычеркнул. "
           "Плата при этом обесточена.",
           font=f_sub, fill=DIM)
    d.text((PAD, PAD + 70),
           "Одинаковый номер в двух строках подряд — это тройник: в ножку идёт "
           "ОДИН провод, разветвлённый пайкой.",
           font=f_sub, fill=NOTE)

    for i, (pin, module, name, note, color) in enumerate(WIRES):
        col, row = i // per_col, i % per_col
        x = PAD + col * col_w
        y = top + row * ROW + ROW / 2

        d.rounded_rectangle((x, y - 11, x + 22, y + 11), radius=5,
                            outline=(88, 94, 106), width=2)
        d.rounded_rectangle((x + 34, y - 14, x + 96, y + 14), radius=7, fill=color)
        d.text((x + 65, y), str(pin), font=f_pin, fill=(255, 255, 255), anchor="mm")
        d.text((x + 112, y - 9), module, font=f_small, fill=DIM, anchor="lm")
        d.text((x + 112, y + 9), name, font=f_mod, fill=color, anchor="lm")
        d.text((x + 288, y + 9), note, font=f_small, fill=DIM, anchor="lm")

    used = {}
    for pin, _, _, _, color in WIRES:
        used.setdefault(pin, color)

    cx = width - PAD - 96
    pi_top = top + (body - pi_h) / 2
    d.rounded_rectangle((cx - 58, pi_top - 14, cx + 58, pi_top + pi_h + 6),
                        radius=12, fill=(30, 32, 37))
    d.text((cx, pi_top - 32), "ГРЕБЁНКА", font=f_head, fill=DIM, anchor="mm")
    for row in range(20):
        for pin in (row * 2 + 1, row * 2 + 2):
            py = pi_top + row * PIN_ROW + PIN_ROW / 2
            px = cx - 48 if pin % 2 else cx + 8
            color = used.get(pin, FREE)
            d.rounded_rectangle((px, py - 11, px + 40, py + 11),
                                radius=2 if pin == 1 else 6, fill=color)
            d.text((px + 20, py), str(pin), font=theme.font(15, bold=True),
                   fill=(255, 255, 255) if pin in used else (104, 110, 120), anchor="mm")
    d.text((cx - 28, pi_top - 4), "1", font=f_small, fill=(150, 155, 165), anchor="mm")

    d.rounded_rectangle((PAD, splitters_y, width - PAD, splitters_y + bh),
                        radius=12, fill=(38, 31, 14), outline=NOTE, width=2)
    d.text((PAD + 24, splitters_y + 24), "ЧЕТЫРЕ ТРОЙНИКА — СПАЯТЬ ДО ПОДКЛЮЧЕНИЯ",
           font=f_head, fill=NOTE)
    d.text((PAD + 24, splitters_y + 48),
           "Режем провод пополам, скручиваем три жилы вместе и пропаиваем одной каплей. "
           "Термоусадку надеть ЗАРАНЕЕ. Ветки по 10 см: длинные ловят помехи на 32 МГц.",
           font=f_leg, fill=(214, 196, 156))
    for i, (pin, a, b, color) in enumerate(SPLITTERS):
        yy = splitters_y + 86 + i * 34
        d.rounded_rectangle((PAD + 24, splitters_y + 73 + i * 34,
                             PAD + 86, splitters_y + 99 + i * 34), radius=7, fill=color)
        d.text((PAD + 55, yy), str(pin), font=f_pin, fill=(255, 255, 255), anchor="mm")
        d.text((PAD + 104, yy), f"{a}    +    {b}", font=f_mod, fill=INK, anchor="lm")

    ly = splitters_y + bh + 26
    d.text((PAD, ly), "Цвета", font=f_head, fill=DIM)
    for i, (label, color) in enumerate(LEGEND):
        x = PAD + (i % 4) * 250
        y = ly + 28 + (i // 4) * 24
        d.rounded_rectangle((x, y - 7, x + 18, y + 7), radius=4, fill=color)
        d.text((x + 28, y), label, font=f_leg, fill=(214, 218, 226), anchor="lm")

    nx = PAD + 4 * 250 + 30
    d.text((nx, ly), "Не подключаем", font=f_head, fill=DIM)
    for i, (module, name) in enumerate(NOT_CONNECTED):
        d.text((nx, ly + 28 + i * 24), f"{name}   ({module})", font=f_leg,
               fill=(120, 126, 136), anchor="lm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    print(f"  {OUT}")
    print(f"  проводов: {len(WIRES)}, тройников: {len(SPLITTERS)}, "
          f"занято ножек: {len(used)} из 40")


if __name__ == "__main__":
    main()
