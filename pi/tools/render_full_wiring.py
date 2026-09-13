"""Общая схема подключения: все узлы блока на одной картинке.

    py tools/render_full_wiring.py

Схема экрана была на один модуль, а собирать придётся четыре. Держать в
голове двадцать три провода, сверяясь с четырьмя таблицами, — верный способ
промахнуться мимо ножки и потом искать это среди всего.

Гребёнка в центре, модули вокруг, цвет — по устройству. Внизу отдельно
разобраны три тройника: их придётся спаять, потому что тач делит с экраном
три линии, а два провода Dupont в одну ножку не втыкаются.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme

OUT = Path(__file__).resolve().parents[1] / "preview" / "подключение-всего.png"

BG = (12, 12, 14)
INK = (238, 238, 240)
DIM = (116, 122, 132)
FREE = (40, 44, 52)
NOTE = (235, 190, 90)

DISPLAY = (64, 150, 240)
TOUCH = (168, 150, 244)
ENCODER = (226, 168, 60)
RADAR = (232, 110, 64)
AIR = (46, 178, 128)
POWER = (236, 104, 104)
GROUND = (134, 142, 154)

# Модуль: (заголовок, цвет рамки, [(ножка модуля, ножка Pi, подпись, цвет)])
# None вместо ножки Pi — не подключаем.
SCREEN = ("ЭКРАН ST7796S  +  ТАЧ", DISPLAY, [
    ("T_IRQ", None, "не подключаем", None),
    ("T_DO", 21, "тройник с SDO", TOUCH),
    ("T_DIN", 19, "тройник с SDI", TOUCH),
    ("T_CS", 26, "GPIO7 · CE1", TOUCH),
    ("T_CLK", 23, "тройник с SCK", TOUCH),
    ("SDO(MISO)", 21, "GPIO9", DISPLAY),
    ("LED", 12, "GPIO18 · ШИМ", DISPLAY),
    ("SCK", 23, "GPIO11", DISPLAY),
    ("SDI(MOSI)", 19, "GPIO10", DISPLAY),
    ("DC/RS", 22, "GPIO25", DISPLAY),
    ("RESET", 18, "GPIO24", DISPLAY),
    ("CS", 24, "GPIO8 · CE0", DISPLAY),
    ("GND", 6, "общий", GROUND),
    ("VCC", 17, "3,3 В", POWER),
])

ENCODER_MOD = ("ЭНКОДЕР KY-040", ENCODER, [
    ("+", 1, "3,3 В", POWER),
    ("GND", 9, "общий", GROUND),
    ("CLK", 11, "GPIO17", ENCODER),
    ("DT", 13, "GPIO27", ENCODER),
    ("SW", 15, "GPIO22", ENCODER),
])

RADAR_MOD = ("РАДАР LD2410", RADAR, [
    ("VCC", 4, "5 В — не 3,3!", POWER),
    ("GND", 20, "общий", GROUND),
    ("TX", 10, "GPIO15 — это RX платы", RADAR),
    ("RX", 8, "GPIO14 — это TX платы", RADAR),
    ("OUT", None, "не подключаем", None),
])

AIR_MOD = ("ДАТЧИК ВОЗДУХА SCD41", AIR, [
    ("VCC", 1, "3,3 В — тройник с энкодером", POWER),
    ("GND", 14, "общий", GROUND),
    ("SDA", 3, "GPIO2", AIR),
    ("SCL", 5, "GPIO3", AIR),
])

PI_NAMES = {
    1: "3.3V", 2: "5V", 3: "GPIO2", 4: "5V", 5: "GPIO3", 6: "GND", 7: "GPIO4",
    8: "GPIO14", 9: "GND", 10: "GPIO15", 11: "GPIO17", 12: "GPIO18", 13: "GPIO27",
    14: "GND", 15: "GPIO22", 16: "GPIO23", 17: "3.3V", 18: "GPIO24", 19: "GPIO10",
    20: "GND", 21: "GPIO9", 22: "GPIO25", 23: "GPIO11", 24: "GPIO8", 25: "GND",
    26: "GPIO7", 27: "ID_SD", 28: "ID_SC", 29: "GPIO5", 30: "GND", 31: "GPIO6",
    32: "GPIO12", 33: "GPIO13", 34: "GND", 35: "GPIO19", 36: "GPIO16", 37: "GPIO26",
    38: "GPIO20", 39: "GND", 40: "GPIO21",
}

# Тройники: (в какую ножку Pi, что сходится, цвет)
SPLITTERS = [
    (23, ["экран SCK", "тач T_CLK"], DISPLAY),
    (19, ["экран SDI(MOSI)", "тач T_DIN"], DISPLAY),
    (21, ["экран SDO(MISO)", "тач T_DO"], DISPLAY),
    # Ножек 3,3 В у Pi всего две, а нужны они трём узлам: экрану, энкодеру
    # и датчику воздуха. Экрану отдаём pin 17 целиком, двое других делят pin 1.
    (1, ["энкодер +", "датчик воздуха VCC"], POWER),
]

ROW = 30
PIN_ROW = 32
PAD = 36


def module_block(d, x, y, title, color, rows, fonts, align_right):
    """Рисует модуль. Возвращает {имя ножки: (x провода, y)}."""
    f_name, f_small, f_pin, f_head = fonts
    w = 320
    h = 44 + len(rows) * ROW + 12
    d.rounded_rectangle((x, y, x + w, y + h), radius=12,
                        fill=(26, 27, 31), outline=color, width=2)
    d.text((x + 18, y + 20), title, font=f_head, fill=color, anchor="lm")

    anchors = {}
    for i, (pin_name, pi_pin, note, wire) in enumerate(rows):
        ry = y + 44 + i * ROW + ROW / 2
        c = wire or FREE
        # Квадратик ножки на стороне, обращённой к гребёнке
        px = (x + w - 16) if align_right else (x + 4)
        d.rounded_rectangle((px, ry - 8, px + 12, ry + 8), radius=3, fill=c)
        anchors[pin_name] = ((px + 12 if align_right else px), ry, c, pi_pin)

        tx = x + 18 if align_right else x + 26
        anchor = "lm"
        d.text((tx, ry - 7), pin_name, font=f_name if pi_pin else f_small,
               fill=c if pi_pin else DIM, anchor=anchor)
        label = f"pin {pi_pin}" if isinstance(pi_pin, int) else ""
        if label:
            d.text((tx, ry + 9), f"{label}  ·  {note}", font=f_small, fill=DIM, anchor=anchor)
        else:
            d.text((tx, ry + 9), note, font=f_small, fill=(84, 88, 98), anchor=anchor)
    return anchors, h


def main() -> None:
    f_head = theme.font(15, bold=True)
    f_name = theme.font(16, bold=True)
    f_small = theme.font(12)
    f_pin = theme.font(13, bold=True)
    f_title = theme.font(28, bold=True)
    f_sub = theme.font(15)
    f_note = theme.font(14)
    fonts = (f_name, f_small, f_pin, f_head)

    width, height = 1560, 1180
    canvas = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(canvas)

    d.text((PAD, PAD), "Desk Companion — подключение всех узлов", font=f_title, fill=INK)
    d.text((PAD, PAD + 40),
           "Гребёнка в центре, вид сверху: microSD вверху, разъёмы слева. "
           "Цвет — по устройству.",
           font=f_sub, fill=DIM)
    d.text((PAD, PAD + 64),
           "Считать ножки только в этой ориентации: штырьки торчат снизу, "
           "но переворачивать плату нельзя — столбцы поменяются местами.",
           font=f_sub, fill=NOTE)

    top = PAD + 110
    cx = width // 2

    # ── гребёнка по центру
    pi_h = 20 * PIN_ROW
    pi_top = top + 40
    d.rounded_rectangle((cx - 52, pi_top - 14, cx + 52, pi_top + pi_h + 6),
                        radius=12, fill=(32, 34, 39))
    d.text((cx, pi_top - 30), "ГРЕБЁНКА Pi  2×20", font=f_head, fill=DIM, anchor="mm")

    used = {}
    for _, _, rows in (SCREEN, ENCODER_MOD, RADAR_MOD, AIR_MOD):
        for _, pi_pin, _, wire in rows:
            if isinstance(pi_pin, int):
                used.setdefault(pi_pin, wire)

    pi_xy = {}
    for row in range(20):
        for pin in (row * 2 + 1, row * 2 + 2):
            py = pi_top + row * PIN_ROW + PIN_ROW / 2
            px = cx - 30 if pin % 2 else cx + 8
            pi_xy[pin] = (px, py)
            color = used.get(pin, FREE)
            d.rounded_rectangle((px, py - 10, px + 22, py + 10),
                                radius=2 if pin == 1 else 5, fill=color)
            d.text((px + 11, py), str(pin), font=f_pin,
                   fill=(255, 255, 255) if pin in used else (104, 110, 120), anchor="mm")

    # ── модули
    left_x, right_x = PAD, width - PAD - 320
    screen_anchors, sh = module_block(d, left_x, top, *SCREEN[:2], SCREEN[2], fonts, True)
    enc_anchors, eh = module_block(d, left_x, top + sh + 26, *ENCODER_MOD[:2],
                                   ENCODER_MOD[2], fonts, True)
    radar_anchors, rh = module_block(d, right_x, top, *RADAR_MOD[:2], RADAR_MOD[2],
                                     fonts, False)
    air_anchors, ah = module_block(d, right_x, top + rh + 26, *AIR_MOD[:2], AIR_MOD[2],
                                   fonts, False)

    # ── провода
    def wire(anchors, from_left: bool, channel_base: int):
        slot = 0
        for name, (ax, ay, color, pi_pin) in anchors.items():
            if not isinstance(pi_pin, int):
                continue
            bx, by = pi_xy[pi_pin]
            near_col = (pi_pin % 2 == 1) == from_left
            bx = bx - 6 if pi_pin % 2 else bx + 28
            chan = channel_base + slot * 17 * (1 if from_left else -1)
            slot += 1
            d.line([(ax, ay), (chan, ay)], fill=color, width=3)
            if near_col:
                d.line([(chan, ay), (chan, by)], fill=color, width=3)
                d.line([(chan, by), (bx, by)], fill=color, width=3)
            else:
                # К дальнему столбцу заходим поверху, в промежутке между
                # рядами: иначе провод прошёл бы прямо по чужой ножке.
                gap = by - PIN_ROW / 2 + 3
                d.line([(chan, ay), (chan, gap)], fill=color, width=3)
                d.line([(chan, gap), (bx, gap)], fill=color, width=3)
                d.line([(bx, gap), (bx, by - 9)], fill=color, width=3)
            d.ellipse((ax - 4, ay - 4, ax + 4, ay + 4), fill=color)

    wire(screen_anchors, True, left_x + 336)
    wire(enc_anchors, True, left_x + 336 + 14 * 17)
    wire(radar_anchors, False, right_x - 16)
    wire(air_anchors, False, right_x - 16 - 5 * 17)

    # ── тройники
    by = pi_top + pi_h + 60
    bh = 62 + len(SPLITTERS) * 34
    d.rounded_rectangle((PAD, by, width - PAD, by + bh), radius=12,
                        fill=(38, 31, 14), outline=NOTE, width=2)
    d.text((PAD + 22, by + 22), "ЧЕТЫРЕ ТРОЙНИКА — ИХ НАДО СПАЯТЬ",
           font=f_head, fill=NOTE)
    d.text((PAD + 22, by + 44),
           "Два провода в одну ножку гребёнки не втыкаются. Режем провод пополам, "
           "к одному концу припаиваем два других, закрываем термоусадкой.",
           font=f_note, fill=(214, 196, 156))
    for i, (pin, parts, color) in enumerate(SPLITTERS):
        yy = by + 76 + i * 34
        d.rounded_rectangle((PAD + 22, yy - 12, PAD + 96, yy + 12), radius=6, fill=color)
        d.text((PAD + 59, yy), f"pin {pin}", font=f_pin, fill=(255, 255, 255), anchor="mm")
        d.text((PAD + 116, yy), "—   " + "   +   ".join(parts), font=f_name,
               fill=INK, anchor="lm")
        d.text((PAD + 620, yy), PI_NAMES[pin], font=f_small, fill=DIM, anchor="lm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    total = sum(1 for _, _, rows in (SCREEN, ENCODER_MOD, RADAR_MOD, AIR_MOD)
                for _, p, _, _ in rows if isinstance(p, int))
    print(f"  {OUT}")
    print(f"  проводов на схеме: {total}, тройников: {len(SPLITTERS)}")


if __name__ == "__main__":
    main()
