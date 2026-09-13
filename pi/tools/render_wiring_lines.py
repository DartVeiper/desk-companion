"""Схема линиями — по одному модулю на панель.

    py tools/render_wiring_lines.py

Одна общая гребёнка на четыре модуля не работает: двадцать шесть линий
сходятся в одну точку и становятся неразличимы. Но линии сами по себе
читаются отлично — просто им нужно место.

Поэтому у каждого модуля своя копия гребёнки. Линий на панели от четырёх до
тринадцати, каждая своего цвета, пересечений почти нет. Цена — гребёнка
нарисована четыре раза, зато любую панель можно закрыть ладонью и она не
мешает остальным.

Порядок втыкания и галочки — в маршрутном листе (render_wiring_sheet.py).
Здесь видно, как провод идёт.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme

OUT = Path(__file__).resolve().parents[1] / "preview" / "подключение-линиями.png"

BG = (12, 12, 14)
INK = (238, 238, 240)
DIM = (120, 126, 136)
FREE = (38, 42, 50)
NOTE = (235, 190, 90)

# Панель: (заголовок, цвет рамки, примечание, [(ножка модуля, ножка Pi, подпись, цвет провода)])
PANELS = [
    ("ЭКРАН ST7796S  +  ТАЧ XPT2046", (64, 150, 240),
     "Тач делит с экраном три линии — они идут через тройники",
     [
         ("T_IRQ", None, "не подключаем", None),
         ("T_DO", 21, "тройник с SDO", (196, 130, 240)),
         ("T_DIN", 19, "тройник с SDI", (150, 130, 245)),
         ("T_CS", 26, "GPIO7 · CE1", (120, 110, 250)),
         ("T_CLK", 23, "тройник с SCK", (176, 152, 255)),
         ("SDO (MISO)", 21, "GPIO9", (90, 200, 255)),
         ("LED", 12, "GPIO18 · подсветка", (250, 190, 70)),
         ("SCK", 23, "GPIO11", (60, 150, 245)),
         ("SDI (MOSI)", 19, "GPIO10", (70, 205, 215)),
         ("DC / RS", 22, "GPIO25", (60, 190, 130)),
         ("RESET", 18, "GPIO24", (160, 210, 80)),
         ("CS", 24, "GPIO8 · CE0", (240, 130, 200)),
         ("GND", 6, "земля, свой провод", (150, 158, 170)),
         ("VCC", 17, "3,3 В", (240, 100, 100)),
     ]),
    ("ЭНКОДЕР KY-040", (226, 168, 60),
     "Питание делит ножку 1 с датчиком воздуха",
     [
         ("+", 1, "3,3 В · тройник", (240, 100, 100)),
         ("GND", 9, "земля, свой провод", (150, 158, 170)),
         ("CLK", 11, "GPIO17", (250, 190, 70)),
         ("DT", 13, "GPIO27", (235, 160, 50)),
         ("SW", 15, "GPIO22 · кнопка", (225, 130, 40)),
     ]),
    ("РАДАР LD2410", (232, 110, 64),
     "Питание 5 В. Линии TX и RX перекрещиваются!",
     [
         ("VCC", 4, "5 В — не 3,3!", (240, 100, 100)),
         ("GND", 20, "земля, свой провод", (150, 158, 170)),
         ("TX", 10, "в RX платы · GPIO15", (250, 140, 80)),
         ("RX", 8, "в TX платы · GPIO14", (230, 100, 55)),
         ("OUT", None, "не подключаем", None),
     ]),
    ("ДАТЧИК ВОЗДУХА SCD41", (46, 178, 128),
     "Питание делит ножку 1 с энкодером",
     [
         ("VCC", 1, "3,3 В · тройник", (240, 100, 100)),
         ("GND", 14, "земля, свой провод", (150, 158, 170)),
         ("SDA", 3, "GPIO2", (60, 200, 150)),
         ("SCL", 5, "GPIO3", (40, 170, 120)),
     ]),
]

ROW = 30
PIN_ROW = 21
PAD = 34


def panel(d, x, y, w, title, frame_color, note, rows, fonts):
    """Одна панель: модуль слева, своя гребёнка справа, линии между ними."""
    f_name, f_small, f_pin, f_head, f_note = fonts

    mod_h = 40 + len(rows) * ROW + 10
    pi_h = 20 * PIN_ROW
    inner = max(mod_h, pi_h) + 54
    d.rounded_rectangle((x, y, x + w, y + inner), radius=12,
                        fill=(21, 22, 26), outline=frame_color, width=2)
    d.text((x + 20, y + 22), title, font=f_head, fill=frame_color, anchor="lm")
    d.text((x + 20, y + 42), note, font=f_note, fill=DIM, anchor="lm")

    body = y + 58
    mod_x, mod_w = x + 20, 250
    mod_y = body + max(0, (pi_h - mod_h) / 2)
    d.rounded_rectangle((mod_x, mod_y, mod_x + mod_w, mod_y + mod_h),
                        radius=9, fill=(30, 32, 37))

    anchors = {}
    for i, (name, pin, label, color) in enumerate(rows):
        ry = mod_y + 34 + i * ROW
        c = color or FREE
        px = mod_x + mod_w - 13
        d.rounded_rectangle((px, ry - 7, px + 11, ry + 7), radius=3, fill=c)
        anchors[name] = (px + 11, ry, c, pin)
        d.text((mod_x + 14, ry - 6), name, font=f_name if pin else f_small,
               fill=c if pin else DIM, anchor="lm")
        tail = f"pin {pin}  ·  {label}" if pin else label
        d.text((mod_x + 14, ry + 8), tail, font=f_small,
               fill=DIM if pin else (78, 82, 92), anchor="lm")

    used = {p: c for _, p, _, c in rows if p}
    cx = x + w - 96
    pi_top = body + max(0, (mod_h - pi_h) / 2)
    d.rounded_rectangle((cx - 44, pi_top - 10, cx + 44, pi_top + pi_h + 4),
                        radius=9, fill=(30, 32, 37))
    pi_xy = {}
    for row in range(20):
        for pin in (row * 2 + 1, row * 2 + 2):
            py = pi_top + row * PIN_ROW + PIN_ROW / 2
            ppx = cx - 36 if pin % 2 else cx + 4
            pi_xy[pin] = (ppx, py)
            c = used.get(pin, FREE)
            d.rounded_rectangle((ppx, py - 8, ppx + 32, py + 8),
                                radius=2 if pin == 1 else 4, fill=c)
            d.text((ppx + 16, py), str(pin), font=f_pin,
                   fill=(255, 255, 255) if pin in used else (100, 106, 116), anchor="mm")

    wires = [(n, p, c) for n, (_, _, c, p) in
             ((r[0], anchors[r[0]]) for r in rows) if p]
    chan_lo = mod_x + mod_w + 16
    chan_hi = cx - 58
    step = (chan_hi - chan_lo) / max(1, len(wires))
    for slot, (name, pin, color) in enumerate(sorted(wires, key=lambda t: t[1])):
        ax, ay, _, _ = anchors[name]
        bx, by = pi_xy[pin]
        chan = chan_lo + slot * step
        near = pin % 2 == 1
        tx = bx - 5 if near else bx + 37
        d.line([(ax, ay), (chan, ay)], fill=color, width=3)
        if near:
            d.line([(chan, ay), (chan, by)], fill=color, width=3)
            d.line([(chan, by), (tx, by)], fill=color, width=3)
        else:
            gap = by - PIN_ROW / 2 + 2
            d.line([(chan, ay), (chan, gap)], fill=color, width=3)
            d.line([(chan, gap), (tx, gap)], fill=color, width=3)
            d.line([(tx, gap), (tx, by - 7)], fill=color, width=3)
        d.ellipse((ax - 4, ay - 4, ax + 4, ay + 4), fill=color)
    return inner


def main() -> None:
    fonts = (theme.font(15, bold=True), theme.font(11), theme.font(11, bold=True),
             theme.font(15, bold=True), theme.font(12))

    small_w = 640
    width = PAD * 2 + small_w * 3 + 48
    big_w = width - PAD * 2
    top = PAD + 84

    probe = Image.new("RGB", (10, 10))
    pd = ImageDraw.Draw(probe)
    big_h = panel(pd, 0, 0, big_w, *PANELS[0][:3], PANELS[0][3], fonts)
    small_h = panel(pd, 0, 0, small_w, *PANELS[1][:3], PANELS[1][3], fonts)
    height = top + big_h + 24 + small_h + PAD

    canvas = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(canvas)
    d.text((PAD, PAD), "Desk Companion — подключение линиями, по модулям",
           font=theme.font(28, bold=True), fill=INK)
    d.text((PAD, PAD + 42),
           "У каждого модуля своя копия гребёнки: так линий мало и они не "
           "путаются. Ориентация везде одна — microSD вверху, разъёмы слева.",
           font=theme.font(14), fill=DIM)
    d.text((PAD, PAD + 62),
           "Ножки 1, 19, 21 и 23 повторяются в двух панелях — это тройники: "
           "в ножку идёт ОДИН провод, разветвлённый пайкой.",
           font=theme.font(14), fill=NOTE)

    panel(d, PAD, top, big_w, *PANELS[0][:3], PANELS[0][3], fonts)
    y2 = top + big_h + 24
    for i, spec in enumerate(PANELS[1:]):
        panel(d, PAD + i * (small_w + 24), y2, small_w, *spec[:3], spec[3], fonts)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT)
    total = sum(1 for _, _, _, rows in PANELS for _, p, _, _ in rows if p)
    print(f"  {OUT}")
    print(f"  панелей: {len(PANELS)}, проводов: {total}")


if __name__ == "__main__":
    main()
