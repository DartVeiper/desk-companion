"""Схема подключения экрана — картинкой.

    py tools/render_display_wiring.py

Таблица «модуль → ножка» при подключении читается плохо: взгляд теряет
строку, а ошибка на один контакт потом ищется среди девяти проводов. Здесь
ножки стоят в том же порядке, что на железе, и каждый провод — своего цвета
от начала до конца.

Все подписи держим на стороне модуля: у гребёнки в одной строке стоят сразу
две ножки, и подписи к ним наезжают друг на друга. Справа поэтому только
номера — по ним и считают, когда втыкают провод.

Отмечены три провода, с которых начинаем: они образуют безопасную проверку
питания, не задействующую ни SPI, ни наш драйвер.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme

OUT = Path(__file__).resolve().parents[1] / "preview" / "подключение-экрана.png"

BG = (13, 13, 13)
INK = (235, 235, 235)
DIM = (105, 110, 120)
FREE = (44, 48, 56)
TEST = (235, 190, 90)

# Ножки модуля сверху вниз — ровно как на плате: T_IRQ с краю, VCC с другого.
# (имя, ножка Pi, что это за ножка Pi, цвет провода, входит ли в проверку питания)
MODULE = [
    ("T_IRQ",     None, "не подключаем",   None,            False),
    ("T_DO",      None, "шаг тача",        None,            False),
    ("T_DIN",     None, "шаг тача",        None,            False),
    ("T_CS",      None, "шаг тача",        None,            False),
    ("T_CLK",     None, "шаг тача",        None,            False),
    ("SDO(MISO)", 21,   "GPIO9",           (166, 150, 240), False),
    ("LED",       12,   "GPIO18 · ШИМ",    (232, 178, 70),  True),
    ("SCK",       23,   "GPIO11",          (70, 150, 240),  False),
    ("SDI(MOSI)", 19,   "GPIO10",          (80, 195, 215),  False),
    ("DC/RS",     22,   "GPIO25",          (40, 175, 125),  False),
    ("RESET",     18,   "GPIO24",          (155, 205, 80),  False),
    ("CS",        24,   "GPIO8 · CE0",     (225, 130, 210), False),
    ("GND",       6,    "общий",           (150, 158, 170), True),
    ("VCC",       2,    "5 В",             (235, 100, 100), True),
]

# Проверка питания. Ножки НЕ те же, что в основной схеме: VCC и LED уходят
# на 3,3 В, потому что пять вольт могут оказаться смертельными для модуля,
# а три вольта безопасны при любом ответе.
TEST_WIRES = (
    ("VCC", 17, "3,3 В — заведомо безопасно"),
    ("GND", 6, "общий"),
    ("LED", 1, "3,3 В — подсветка напрямую"),
)

NOTES = (
    ("Ножки Pi считай, глядя на плату СВЕРХУ — со стороны чипа, как на", (255, 200, 120)),
    ("схеме. Штырьки у нас торчат снизу, но переворачивать плату нельзя:", (255, 200, 120)),
    ("столбцы поменяются местами, и всё уйдёт зеркально.", (255, 200, 120)),
    ("", BG),
    ("Пять ножек тача пока не трогаем. На своём шаге три из них придут", INK),
    ("на уже занятые 23, 19 и 21 — понадобится разветвитель.", INK),
)

MOD_ROW, PI_ROW = 40, 34
PAD = 40


def main() -> None:
    f_pin = theme.font(14, bold=True)
    f_name = theme.font(17, bold=True)
    f_dest = theme.font(14, bold=True)
    f_small = theme.font(13)
    f_head = theme.font(24, bold=True)
    f_sub = theme.font(15)

    wires = [(n, p, c) for n, p, _, c, _ in MODULE if p]
    chan_w = 26 * len(wires)

    mod_right = PAD + 330                    # правый край планки модуля
    chan_lo = mod_right + 34
    pi_cx = chan_lo + chan_w + 60            # центр гребёнки
    width = pi_cx + 150
    top = PAD + 136   # запас под две строки подзаголовка и подписи над колонками
    mod_h, pi_h = len(MODULE) * MOD_ROW, 20 * PI_ROW
    body = max(mod_h, pi_h)
    height = top + body + 290 + len(NOTES) * 25 + PAD

    canvas = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(canvas)

    d.text((PAD, PAD), "Подключение экрана ST7796S к Raspberry Pi", font=f_head, fill=INK)
    d.text((PAD, PAD + 36),
           "Слева ножки модуля в том порядке, как на плате. Справа гребёнка, вид сверху.",
           font=f_sub, fill=(150, 155, 165))
    d.text((PAD, PAD + 60),
           "Номер ножки Pi написан у каждого провода — у гребёнки только номера, "
           "чтобы подписи не наезжали.",
           font=f_sub, fill=(150, 155, 165))

    # ── планка модуля ──────────────────────────────────────────────────────
    mod_top = top + (body - mod_h) // 2
    d.rounded_rectangle((PAD, mod_top - 14, mod_right + 12, mod_top + mod_h + 6),
                        radius=10, fill=(56, 20, 20))
    d.text((PAD + 16, mod_top - 32), "МОДУЛЬ ЭКРАНА  (14 ножек)", font=f_small,
           fill=(210, 140, 140))

    mod_y = {}
    for i, (name, pin, dest, color, in_test) in enumerate(MODULE):
        y = mod_top + i * MOD_ROW + MOD_ROW // 2
        mod_y[name] = y
        c = color or FREE

        d.text((PAD + 16, y), name, font=f_name if pin else f_small,
               fill=c if pin else DIM, anchor="lm")

        if pin:
            if in_test:
                d.rounded_rectangle((PAD + 146, y - 11, PAD + 196, y + 10),
                                    radius=10, fill=(70, 56, 20))
                d.text((PAD + 171, y), "тест", font=f_small, fill=TEST, anchor="mm")
            d.text((mod_right - 6, y - 8), f"pin {pin}", font=f_dest, fill=c, anchor="rm")
            d.text((mod_right - 6, y + 9), dest, font=f_small, fill=DIM, anchor="rm")
        else:
            d.text((mod_right - 6, y), dest, font=f_small, fill=(78, 82, 92), anchor="rm")

        d.rounded_rectangle((mod_right, y - 9, mod_right + 16, y + 8), radius=4, fill=c)

    # ── гребёнка Pi ────────────────────────────────────────────────────────
    used = {p: c for _, p, c in wires}
    pi_top = top + (body - pi_h) // 2
    d.rounded_rectangle((pi_cx - 46, pi_top - 12, pi_cx + 46, pi_top + pi_h + 4),
                        radius=10, fill=(30, 32, 36))
    d.text((pi_cx, pi_top - 32), "ГРЕБЁНКА Pi", font=f_small, fill=(150, 155, 165), anchor="mm")

    pi_y, pi_x = {}, {}
    for row in range(20):
        for pin in (row * 2 + 1, row * 2 + 2):
            y = pi_top + row * PI_ROW + PI_ROW // 2
            x = pi_cx - 26 if pin % 2 else pi_cx + 8
            pi_y[pin], pi_x[pin] = y, x
            color = used.get(pin, FREE)
            d.rounded_rectangle((x, y - 9, x + 18, y + 8),
                                radius=1 if pin == 1 else 4, fill=color)
            d.text((x + 9, y), str(pin), font=f_pin,
                   fill=(255, 255, 255) if pin in used else (100, 105, 115), anchor="mm")
    d.text((pi_cx - 17, pi_top - 14), "1", font=f_small, fill=(150, 155, 165), anchor="mm")

    # ── провода ────────────────────────────────────────────────────────────
    # Каждому проводу свой вертикальный коридор, иначе линии сливаются.
    # Порядок коридоров — по номеру ножки: так линии почти не пересекаются.
    order = sorted(range(len(wires)), key=lambda i: wires[i][1])
    for slot, i in enumerate(order):
        name, pin, color = wires[i]
        y0, y1 = mod_y[name], pi_y[pin]
        chan = chan_lo + slot * 26
        d.line([(mod_right + 14, y0), (chan, y0)], fill=color, width=3)
        d.line([(chan, y0), (chan, y1)], fill=color, width=3)
        if pin % 2:
            d.line([(chan, y1), (pi_x[pin] - 4, y1)], fill=color, width=3)
        else:
            # К чётному столбцу заходим поверху, в промежутке между рядами,
            # иначе провод прошёл бы прямо по нечётной ножке.
            gap = y1 - PI_ROW // 2 + 3
            d.line([(chan, y1), (chan, gap)], fill=color, width=3)
            d.line([(chan, gap), (pi_x[pin] + 9, gap)], fill=color, width=3)
            d.line([(pi_x[pin] + 9, gap), (pi_x[pin] + 9, y1 - 9)], fill=color, width=3)

    # ── сначала проверка питания ───────────────────────────────────────────
    by = top + body + 40
    bh = 50 + len(TEST_WIRES) * 30
    d.rounded_rectangle((PAD, by, PAD + 700, by + bh), radius=10,
                        fill=(38, 31, 14), outline=TEST, width=2)
    d.text((PAD + 22, by + 16), "СНАЧАЛА — ПРОВЕРКА ПИТАНИЯ, ТРИ ПРОВОДА",
           font=f_dest, fill=TEST)
    for i, (name, pin, why) in enumerate(TEST_WIRES):
        y = by + 48 + i * 30
        d.text((PAD + 22, y), name, font=f_name, fill=INK)
        d.text((PAD + 152, y + 3), f"pin {pin}", font=f_dest, fill=TEST)
        d.text((PAD + 232, y + 3), why, font=f_small, fill=(190, 180, 155))

    explain = (
        "Здесь VCC и LED идут на 3,3 В, а НЕ на pin 2 и pin 12 из схемы выше: "
        "пять вольт могут убить",
        "трёхвольтовый модуль, а три вольта безопасны при любом ответе. "
        "Подсветка зажглась ярко —",
        "модуль на 3,3 В, VCC там и остаётся. Тусклая или мёртвая — "
        "внутри стабилизатор, VCC на pin 2.",
    )
    ey = by + bh + 18
    for i, line in enumerate(explain):
        d.text((PAD, ey + i * 22), line, font=f_small, fill=(215, 195, 150))

    ny = ey + len(explain) * 22 + 22
    for i, (note, color) in enumerate(NOTES):
        if note:
            d.text((PAD, ny + i * 25), note, font=f_sub, fill=color)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    print(f"  {OUT}")
    print(f"  проводов: {len(wires)}, из них в проверке питания: "
          f"{sum(1 for *_, t in MODULE if t)}")


if __name__ == "__main__":
    main()
