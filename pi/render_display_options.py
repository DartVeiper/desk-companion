"""Кандидаты на замену дисплея, все в одном масштабе.

    py render_display_options.py

Активные области вложены от общего угла — так сразу видно, кто насколько
крупнее. Файл разовый, для выбора железа.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from app import theme

PX_PER_MM = 4.2
OUT = Path(__file__).parent / "preview" / "варианты-дисплеев.png"

# (подпись, диагональ дюймы, px по ширине, px по высоте, цвет, интерфейс)
OPTIONS = [
    ("ваш 4\" 480x320", 4.0, 480, 320, (120, 180, 255), "SPI"),
    ("4.3\" 800x480", 4.3, 800, 480, (150, 210, 160), "HDMI"),
    ("5\" 800x480", 5.0, 800, 480, (240, 190, 96), "HDMI"),
    ("5.5\" 1920x1080", 5.5, 1920, 1080, (232, 130, 200), "HDMI"),
    ("7\" 1024x600", 7.0, 1024, 600, (238, 108, 108), "HDMI"),
]
PHONE = ("iPhone 16 Plus", 6.7, 2796, 1290)


def geometry(diag_in: float, px_w: int, px_h: int) -> tuple[float, float, float]:
    diag_mm = diag_in * 25.4
    unit = diag_mm / math.hypot(px_w, px_h)
    w, h = px_w * unit, px_h * unit
    return w, h, px_w / (w / 25.4)


def main() -> None:
    rows = [(name, *geometry(d, pw, ph), color, iface) for name, d, pw, ph, color, iface in OPTIONS]
    phone_w, phone_h, _ = geometry(PHONE[1], PHONE[2], PHONE[3])

    span_w = max(max(r[1] for r in rows), phone_w)
    span_h = max(max(r[2] for r in rows), phone_h)
    pad, legend = 30, 300
    canvas = Image.new(
        "RGB",
        (int(span_w * PX_PER_MM) + pad * 2 + legend, int(span_h * PX_PER_MM) + pad * 2),
        (18, 18, 20),
    )
    draw = ImageDraw.Draw(canvas)
    base_x, base_y = pad, canvas.height - pad

    draw.rectangle(
        (base_x, base_y - phone_h * PX_PER_MM, base_x + phone_w * PX_PER_MM, base_y),
        outline=(80, 86, 98), width=2,
    )
    draw.text((base_x + phone_w * PX_PER_MM - 6, base_y - phone_h * PX_PER_MM - 14),
              f"{PHONE[0]} боком  {phone_w:.0f}x{phone_h:.0f} мм",
              font=theme.font(14), fill=(110, 118, 132), anchor="rs")

    # 4" и 4.3" отличаются по высоте на два миллиметра, поэтому подписи
    # у верхней кромки сливаются — разводим их принудительно.
    taken: list[float] = []
    for name, w, h, ppi, color, iface in sorted(rows, key=lambda r: -r[1]):
        top = base_y - h * PX_PER_MM
        draw.rectangle((base_x, top, base_x + w * PX_PER_MM, base_y), outline=color, width=3)

        label_y = top + 14
        while any(abs(label_y - used) < 22 for used in taken):
            label_y += 22
        taken.append(label_y)
        draw.line((base_x + w * PX_PER_MM, top, base_x + w * PX_PER_MM + 6, label_y - 5),
                  fill=color, width=1)
        draw.text((base_x + w * PX_PER_MM + 10, label_y), name,
                  font=theme.font(15, bold=True), fill=color, anchor="lm")

    ly = pad + 6
    draw.text((canvas.width - legend + 10, ly), "в реальном масштабе",
              font=theme.font(17, bold=True), fill=theme.FG)
    ly += 34
    for name, w, h, ppi, color, iface in rows:
        draw.rectangle((canvas.width - legend + 10, ly + 3, canvas.width - legend + 24, ly + 17), fill=color)
        draw.text((canvas.width - legend + 34, ly), name, font=theme.font(15, bold=True), fill=color)
        draw.text((canvas.width - legend + 34, ly + 20),
                  f"{w:.0f} x {h:.0f} мм · {ppi:.0f} PPI · {iface}",
                  font=theme.font(13), fill=(150, 158, 172))
        ly += 48

    canvas.save(OUT)
    for name, w, h, ppi, _, iface in rows:
        print(f"  {name:20} {w:6.1f} x {h:5.1f} мм   {ppi:4.0f} PPI   {iface}")
    print(f"  {'iPhone 16 Plus':20} {phone_w:6.1f} x {phone_h:5.1f} мм")
    print(f"\n  {OUT}")


if __name__ == "__main__":
    main()
