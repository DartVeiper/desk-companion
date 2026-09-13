"""Разовая калибровка резистивного тача.

    python3 tools/touch_calibrate.py

Рисует крестик, ждёт нажатия — **сколько угодно долго**, без отсчётов. Жми
когда удобно. Достаточно трёх нажатий.

Делает две вещи сразу, потому что данные для обеих берутся из одних и тех
же нажатий:

1. **Координаты.** Панель отдаёт не пиксели, а сырые отсчёты, и границы у
   каждого экземпляра свои. Без калибровки тап уезжает на десятки пикселей.

2. **Чувствительность.** Порог `z1_min` отделяет касание от шума. Слишком
   высокий — приходится давить изо всех сил; слишком низкий — панель
   «нажимает» сама. Здесь он не угадывается: сначала меряется покой, потом
   ваши настоящие нажатия, и порог ставится между.

Результат записывается прямо в `app/config.toml` — переписывать руками
ничего не нужно.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _service  # noqa: E402

from app import theme  # noqa: E402
from app.drivers import xpt2046  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"
MARGIN = 34
SETTLE_S = 0.4      # пауза после отпускания, чтобы не поймать дребезг
IDLE_SAMPLES = 80   # сколько замеров покоя снять перед началом


def target(display, x: int, y: int, caption: str, hint: str = "") -> None:
    frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
    draw = ImageDraw.Draw(frame)
    draw.line((x - 20, y, x + 20, y), fill=theme.ACCENT, width=3)
    draw.line((x, y - 20, x, y + 20), fill=theme.ACCENT, width=3)
    draw.ellipse((x - 8, y - 8, x + 8, y + 8), outline=theme.ACCENT, width=3)
    draw.text((theme.WIDTH // 2, theme.HEIGHT - 54), caption,
              font=theme.font(theme.BODY, bold=True), fill=theme.FG, anchor="mm")
    if hint:
        draw.text((theme.WIDTH // 2, theme.HEIGHT - 26), hint,
                  font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")
    display.show(frame)


def measure_idle(panel: xpt2046.Xpt2046) -> int:
    """Максимальный z1 нетронутой панели. Ниже него всё — заведомо шум."""
    peak = 0
    for _ in range(IDLE_SAMPLES):
        peak = max(peak, panel._read(xpt2046.CMD_Z1))
        time.sleep(0.02)
    return peak


def wait_press(panel: xpt2046.Xpt2046, floor: int) -> tuple[int, int, int]:
    """Ждать нажатия без ограничения по времени.

    Порог здесь заведомо низкий — задача не отфильтровать, а поймать даже
    лёгкое нажатие и узнать, какой z1 оно даёт. Настоящий порог считается
    потом, по собранным числам.
    """
    while True:
        if panel._read(xpt2046.CMD_Z1) >= floor:
            peak, raw = 0, None
            while True:
                z1 = panel._read(xpt2046.CMD_Z1)
                if z1 < floor:
                    break
                if z1 > peak:
                    peak, raw = z1, panel.raw()
                time.sleep(0.02)
            time.sleep(SETTLE_S)
            if raw is not None:
                return raw[0], raw[1], peak
        time.sleep(0.02)


def write_config(values: dict[str, object]) -> None:
    """Вписать значения в блок [touch], не трогая остальной конфиг."""
    text = CONFIG.read_text(encoding="utf-8")
    for key, value in values.items():
        literal = str(value).lower() if isinstance(value, bool) else str(value)
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(f"{key} = {literal}", text, count=1)
        else:
            text = text.replace("[touch]", f"[touch]\n{key} = {literal}", 1)
    CONFIG.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    try:
        import spidev
    except ImportError:
        print("\n  нужен spidev — запускать на самом Pi\n")
        raise SystemExit(1)

    _service.require_stopped()

    cfg = load_config(CONFIG)["touch"]
    from app.display.st7796s import open_spi

    display = open_spi(speed_hz=cfg.get("display_speed_hz", 16_000_000))

    spi = spidev.SpiDev()
    spi.open(cfg["spi_bus"], cfg["spi_device"])
    spi.max_speed_hz = cfg["speed_hz"]
    spi.mode = 0
    panel = xpt2046.Xpt2046(lambda payload: bytes(spi.xfer2(list(payload))))

    print("\n  Калибровка тача. Жми когда удобно — отсчёта нет.\n")

    target(display, theme.WIDTH // 2, theme.HEIGHT // 2,
           "не трогай панель", "меряю покой, пара секунд")
    idle_peak = measure_idle(panel)
    print(f"  покой: z1 не выше {idle_peak}")

    floor = max(idle_peak + 2, 8)
    presses = []
    corners = []
    for x, y, caption in (
        (MARGIN, MARGIN, "жми точно в крестик"),
        (theme.WIDTH - MARGIN, theme.HEIGHT - MARGIN, "теперь в этот"),
    ):
        target(display, x, y, caption, "нажимай как привычно, без усилия")
        raw_x, raw_y, peak = wait_press(panel, floor)
        corners.append((raw_x, raw_y))
        presses.append(peak)
        print(f"  угол ({x:3}, {y:3}) -> сырые ({raw_x:4}, {raw_y:4}), z1 {peak}")

    calibration = xpt2046.calibration_from_corners(corners[0], corners[1])

    target(display, theme.WIDTH // 2, theme.HEIGHT // 2,
           "проверка: жми в центр", "нажимай как привычно")
    raw_x, raw_y, peak = wait_press(panel, floor)
    presses.append(peak)
    got = xpt2046.to_screen(raw_x, raw_y, calibration, theme.WIDTH, theme.HEIGHT)
    error = ((got[0] - theme.WIDTH // 2) ** 2 + (got[1] - theme.HEIGHT // 2) ** 2) ** 0.5

    # Порог ставим ближе к покою, чем к нажатию: лишняя чувствительность
    # здесь дешевле, чем необходимость давить. Шум покоя уже измерен, и
    # опускаться ниже него нельзя — вернутся ложные касания.
    weakest = min(presses)
    z1_min = max(idle_peak + 2, int(idle_peak + (weakest - idle_peak) * 0.25))

    print(f"\n  проверка центра: получилось {got}, промах {error:.0f} px")
    if error > 25:
        print("  промах великоват — стоит перекалибровать, целясь точнее")

    print(f"  нажатия дали z1 от {min(presses)} до {max(presses)}")
    print(f"  порог z1_min: {z1_min}  (было {cfg.get('z1_min', '—')})")

    values = dict(calibration.to_dict())
    values["z1_min"] = z1_min
    write_config(values)

    target(display, theme.WIDTH // 2, theme.HEIGHT // 2, "готово", "всё записано в конфиг")
    time.sleep(1.5)
    display.close()
    spi.close()

    print("\n  Записано в app/config.toml. Осталось перезапустить сервис:")
    print("      sudo systemctl restart desk-companion\n")


if __name__ == "__main__":
    main()
