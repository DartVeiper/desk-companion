"""Схема подключения к гребёнке — картинкой, а не таблицей.

    py tools/render_pinout.py

Таблицу при пайке читать неудобно: взгляд теряет строку, а ошибка на один
контакт означает искать её потом среди двадцати проводов. На схеме ножки
расположены так же, как на плате, и раскрашены по устройствам.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme

OUT = Path(__file__).resolve().parents[1] / "preview" / "распиновка.png"

# Цвета устройств — из проверенной палитры дашборда, тёмный вариант.
DISPLAY = (57, 135, 229)
TOUCH = (144, 133, 233)
RADAR = (217, 89, 38)
AIR = (25, 158, 112)
ENCODER = (201, 133, 0)
POWER = (232, 103, 103)
GROUND = (120, 128, 140)
FREE = (52, 56, 64)

LEGEND = (
    (DISPLAY, "Экран ST7796S"),
    (TOUCH, "Тач XPT2046"),
    (RADAR, "Радар LD2410"),
    (AIR, "Датчик воздуха SCD41"),
    (ENCODER, "Энкодер KY-040"),
    (POWER, "Питание"),
    (GROUND, "Общий (GND)"),
)

# Физический номер ножки -> (имя ножки, к чему идёт, цвет)
PINS: dict[int, tuple[str, str, tuple[int, int, int]]] = {
    1: ("3.3V", "SCD41 VCC", POWER),
    2: ("5V", "Экран VCC", POWER),
    3: ("GPIO2 SDA", "SCD41 SDA", AIR),
    4: ("5V", "LD2410 VCC", POWER),
    5: ("GPIO3 SCL", "SCD41 SCL", AIR),
    6: ("GND", "Экран GND", GROUND),
    7: ("GPIO4", "", FREE),
    8: ("GPIO14 TX", "LD2410 RX", RADAR),
    9: ("GND", "SCD41 GND", GROUND),
    10: ("GPIO15 RX", "LD2410 TX", RADAR),
    11: ("GPIO17", "Энкодер CLK", ENCODER),
    12: ("GPIO18 PWM", "Экран LED", DISPLAY),
    13: ("GPIO27", "Энкодер DT", ENCODER),
    14: ("GND", "Энкодер GND", GROUND),
    15: ("GPIO22", "Энкодер SW", ENCODER),
    16: ("GPIO23", "", FREE),
    17: ("3.3V", "Энкодер +", POWER),
    18: ("GPIO24", "Экран RESET", DISPLAY),
    19: ("GPIO10 MOSI", "Экран SDI + тач T_DIN", DISPLAY),
    20: ("GND", "LD2410 GND", GROUND),
    21: ("GPIO9 MISO", "Экран SDO + тач T_DO", DISPLAY),
    22: ("GPIO25", "Экран DC", DISPLAY),
    23: ("GPIO11 SCLK", "Экран SCK + тач T_CLK", DISPLAY),
    24: ("GPIO8 CE0", "Экран CS", DISPLAY),
    25: ("GND", "", GROUND),
    26: ("GPIO7 CE1", "Тач T_CS", TOUCH),
    27: ("ID_SD", "", FREE),
    28: ("ID_SC", "", FREE),
    29: ("GPIO5", "", FREE),
    30: ("GND", "", GROUND),
    31: ("GPIO6", "", FREE),
    32: ("GPIO12", "", FREE),
    33: ("GPIO13", "", FREE),
    34: ("GND", "", GROUND),
    35: ("GPIO19", "", FREE),
    36: ("GPIO16", "", FREE),
    37: ("GPIO26", "", FREE),
    38: ("GPIO20", "", FREE),
    39: ("GND", "", GROUND),
    40: ("GPIO21", "", FREE),
}

NOTES = (
    "Разъём камеры (узкий чёрный на торце платы) НЕ используется — экран идёт по GPIO.",
    "Разъёма DSI у Zero 2 W нет вовсе. Дисплей подключается только этими ножками.",
    "Линии UART перекрещиваются: TX радара в RX платы (pin 10), RX радара в TX (pin 8).",
    "Радар питается от 5 В (pin 2 или 4). От 3.3 В он не заработает.",
    "Тач делит SCK, MOSI и MISO с экраном, но имеет свой чип-селект на pin 26.",
)

ROW_H, PIN_W, GAP = 30, 26, 300
PAD = 34


def main() -> None:
    font_pin = theme.font(15, bold=True)
    font_name = theme.font(14)
    font_use = theme.font(15, bold=True)
    font_head = theme.font(20, bold=True)
    font_note = theme.font(14)
    draw_probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    # Ширину задаём от самой длинной строки примечаний, а не от схемы:
    # схема узкая, а текст под ней — нет, и он обрезался.
    notes_width = max(draw_probe.textlength("•  " + note, font=font_note) for note in NOTES)
    legend_width = 26 + max(draw_probe.textlength(label, font=font_note) for _, label in LEGEND)
    width = int(max(PAD * 2 + GAP * 2 + PIN_W * 2 + 40,
                    PAD * 2 + legend_width + 40 + notes_width))
    height = (PAD + 70 + 20 * ROW_H + 30 + 26
              + max(len(LEGEND) * 24, len(NOTES) * 22) + PAD)
    canvas = Image.new("RGB", (width, height), (13, 13, 13))
    draw = ImageDraw.Draw(canvas)

    draw.text((PAD, PAD), "Desk Companion — подключение к гребёнке 2×20",
              font=font_head, fill=(240, 240, 240))
    draw.text((PAD, PAD + 28),
              "Вид сверху, ножками к себе. Слева нечётные, справа чётные — как на плате.",
              font=font_note, fill=(150, 155, 165))

    top = PAD + 70
    centre = PAD + GAP + PIN_W + 20

    # Пластиковое основание гребёнки
    draw.rounded_rectangle((centre - PIN_W - 14, top - 10,
                            centre + PIN_W + 14, top + 20 * ROW_H + 2),
                           radius=8, fill=(30, 32, 36))

    for row in range(20):
        left_pin, right_pin = row * 2 + 1, row * 2 + 2
        y = top + row * ROW_H + ROW_H // 2

        for pin, is_left in ((left_pin, True), (right_pin, False)):
            name, use, color = PINS[pin]
            x = centre - PIN_W // 2 - 7 if is_left else centre + PIN_W // 2 - 7

            # Квадратик ножки: первая ножка квадратная и на плате тоже —
            # по ней и определяется, где начинается счёт.
            square = pin == 1
            draw.rounded_rectangle((x, y - 8, x + 15, y + 7),
                                   radius=1 if square else 4, fill=color)
            draw.text((x + 7, y), str(pin), font=font_pin,
                      fill=(255, 255, 255) if color != FREE else (110, 115, 125),
                      anchor="mm")

            text_x = centre - PIN_W - 22 if is_left else centre + PIN_W + 22
            anchor = "rm" if is_left else "lm"
            ink = (235, 235, 235) if use else (95, 100, 110)
            draw.text((text_x, y - 7), use or "свободна", font=font_use,
                      fill=color if use else (80, 85, 95), anchor=anchor)
            draw.text((text_x, y + 8), name, font=font_name, fill=ink if use else (70, 74, 82),
                      anchor=anchor)

    draw.text((centre - PIN_W // 2 - 7 + 7, top - 20), "ножка 1", font=font_note,
              fill=(150, 155, 165), anchor="mm")

    legend_y = top + 20 * ROW_H + 30
    draw.text((PAD, legend_y), "Устройства", font=theme.font(15, bold=True), fill=(150, 155, 165))
    for i, (color, label) in enumerate(LEGEND):
        y = legend_y + 26 + i * 24
        draw.rounded_rectangle((PAD, y - 6, PAD + 16, y + 8), radius=4, fill=color)
        draw.text((PAD + 26, y), label, font=font_note, fill=(225, 225, 225), anchor="lm")

    notes_y = legend_y + 26
    for i, note in enumerate(NOTES):
        draw.text((PAD + legend_width + 40, notes_y + i * 22), "•  " + note,
                  font=font_note, fill=(200, 205, 215))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    used = sum(1 for _, use, _ in PINS.values() if use)
    print(f"  {OUT}")
    print(f"  задействовано ножек: {used} из 40")


if __name__ == "__main__":
    main()
