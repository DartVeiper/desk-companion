"""Сравнение габаритов экранов в реальном масштабе.

    py render_size_compare.py

Рисует активную область 4" 480x320 рядом с экраном iPhone 16 Plus в
альбомной ориентации, в одном масштабе, с настоящим кадром часов внутри.
Файл разовый — нужен только чтобы решить вопрос с размером дисплея.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from app import theme
from app.screens.clock_concepts import HybridClock
from app.state import Env, Pc, State, Weather

PX_PER_MM = 5.0
OUT = Path(__file__).parent / "preview" / "размер-экранов.png"


def size_mm(diagonal_in: float, ratio_w: float, ratio_h: float) -> tuple[float, float]:
    diag_mm = diagonal_in * 25.4
    unit = diag_mm / math.hypot(ratio_w, ratio_h)
    return ratio_w * unit, ratio_h * unit


def main() -> None:
    ours_w, ours_h = size_mm(4.0, 3, 2)
    phone_w, phone_h = size_mm(6.7, 2796, 1290)
    ppi = 480 / (ours_w / 25.4)

    state = State(
        now=datetime(2026, 8, 19, 14, 32),
        env=Env(co2=680, temperature=23.4, humidity=41),
        weather=Weather(temp=18, cond="облачно"),
        pc=Pc(last_heartbeat=datetime(2026, 8, 19, 14, 32)),
    )
    state.desk.presence = True

    shot = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
    HybridClock().render(state, ImageDraw.Draw(shot), shot)

    pad, gap = 34, 30
    pw, ph = int(phone_w * PX_PER_MM), int(phone_h * PX_PER_MM)
    ow, oh = int(ours_w * PX_PER_MM), int(ours_h * PX_PER_MM)

    canvas = Image.new("RGB", (pad * 2 + pw, pad * 2 + ph + gap + oh + 40), (18, 18, 20))
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((pad, pad, pad + pw, pad + ph), outline=(90, 96, 108), width=2)
    draw.text((pad + pw / 2, pad + ph / 2), "iPhone 16 Plus, боком",
              font=theme.font(19, bold=True), fill=(150, 158, 172), anchor="mm")
    draw.text((pad + pw / 2, pad + ph / 2 + 26), f"{phone_w:.0f} x {phone_h:.0f} мм",
              font=theme.font(15), fill=(110, 118, 132), anchor="mm")

    y = pad + ph + gap
    canvas.paste(shot.resize((ow, oh), Image.LANCZOS), (pad, y))
    draw.rectangle((pad, y, pad + ow, y + oh), outline=(120, 180, 255), width=2)
    draw.text(
        (pad + ow + 22, y + 24),
        f"ваш 4\", активная область\n{ours_w:.0f} x {ours_h:.0f} мм\n{ppi:.0f} PPI",
        font=theme.font(17, bold=True), fill=(120, 180, 255), spacing=8,
    )
    draw.text(
        (pad + ow + 22, y + oh - 34),
        f"по ширине уже в {phone_w / ours_w:.2f} раза",
        font=theme.font(15), fill=(150, 158, 172),
    )

    canvas.save(OUT)
    print(f"  ваш экран : {ours_w:.1f} x {ours_h:.1f} мм, {ppi:.0f} PPI")
    print(f"  iPhone    : {phone_w:.1f} x {phone_h:.1f} мм")
    print(f"  цифры часов при 132 px: {132 / theme.HEIGHT * ours_h:.1f} мм высотой")
    print(f"\n  {OUT}")


if __name__ == "__main__":
    main()
