"""Разовая калибровка резистивного тача.

    py tools/touch_calibrate.py

Рисует крестик в углу, ждёт нажатия, запоминает сырые значения АЦП. Двух
углов достаточно. Результат печатается готовой строкой для config.toml.

Нужно потому, что панель отдаёт не пиксели, а сырые отсчёты, и границы у
каждого экземпляра свои — разброс между платами больше, чем можно списать
на шум. Без калибровки тап уезжает на десятки пикселей, а с резистивной
панелью и без того мазать легко.

Заодно печатает сопротивление касания: по нему подбирается порог
MAX_RESISTANCE, ниже которого касание считается настоящим.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import theme
from app.drivers import xpt2046

MARGIN = 30
SETTLE_S = 0.4  # пауза после отпускания, чтобы не поймать дребезг


def target(display, x: int, y: int, caption: str) -> None:
    frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
    draw = ImageDraw.Draw(frame)
    draw.line((x - 18, y, x + 18, y), fill=theme.ACCENT, width=3)
    draw.line((x, y - 18, x, y + 18), fill=theme.ACCENT, width=3)
    draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline=theme.ACCENT, width=3)
    draw.text((theme.WIDTH // 2, theme.HEIGHT - 42), caption,
              font=theme.font(theme.BODY, bold=True), fill=theme.FG, anchor="mm")
    display.show(frame)


def wait_touch(touch: xpt2046.Xpt2046) -> tuple[int, int, float]:
    """Дождаться уверенного нажатия и вернуть сырые координаты."""
    while True:
        resistance = touch.resistance()
        if resistance < xpt2046.MAX_RESISTANCE:
            raw = touch.raw()
            while touch.resistance() < xpt2046.MAX_RESISTANCE:
                time.sleep(0.02)  # ждём отпускания
            time.sleep(SETTLE_S)
            return raw[0], raw[1], resistance
        time.sleep(0.02)


def main() -> None:
    try:
        import spidev
    except ImportError:
        print("\n  нужен spidev — запускать на самом Pi\n")
        raise SystemExit(1)

    from app.display.st7796s import open_spi

    display = open_spi()

    spi = spidev.SpiDev()
    spi.open(0, 1)                # CE1: у тача свой чип-селект
    spi.max_speed_hz = 2_000_000  # XPT2046 быстрее не держит, экран идёт на 32 МГц
    spi.mode = 0
    touch = xpt2046.Xpt2046(lambda payload: bytes(spi.xfer2(list(payload))))

    print("\n  Калибровка тача. Жмите точно в центр крестика.\n")

    corners = []
    for x, y, caption in (
        (MARGIN, MARGIN, "нажмите верхний левый крестик"),
        (theme.WIDTH - MARGIN, theme.HEIGHT - MARGIN, "теперь нижний правый"),
    ):
        target(display, x, y, caption)
        raw_x, raw_y, resistance = wait_touch(touch)
        corners.append((raw_x, raw_y))
        print(f"  угол ({x:3}, {y:3}) -> сырые ({raw_x:4}, {raw_y:4}), "
              f"сопротивление {resistance:.0f}")

    calibration = xpt2046.calibration_from_corners(corners[0], corners[1])

    target(display, theme.WIDTH // 2, theme.HEIGHT // 2, "проверка: жмите в центр")
    raw_x, raw_y, _ = wait_touch(touch)
    got = xpt2046.to_screen(raw_x, raw_y, calibration, theme.WIDTH, theme.HEIGHT)
    error = ((got[0] - theme.WIDTH // 2) ** 2 + (got[1] - theme.HEIGHT // 2) ** 2) ** 0.5

    print(f"\n  проверка центра: получилось {got}, промах {error:.0f} px")
    if error > 25:
        print("  промах великоват — стоит перекалибровать, целясь точнее")

    print("\n  Скопируйте в app/config.toml:\n")
    print("[touch]")
    for key, value in calibration.to_dict().items():
        print(f"{key} = {str(value).lower() if isinstance(value, bool) else value}")
    print()


if __name__ == "__main__":
    main()
