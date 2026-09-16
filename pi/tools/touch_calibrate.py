"""Разовая калибровка резистивного тача.

    python3 tools/touch_calibrate.py

Рисует крестик, ждёт нажатия — **сколько угодно долго**, без отсчётов. Жми
когда удобно. Нажатий пять: четыре угла и проверка в центре.

Делает две вещи сразу, потому что данные для обеих берутся из одних и тех
же нажатий:

1. **Координаты.** Панель отдаёт не пиксели, а сырые отсчёты, и границы у
   каждого экземпляра свои. Без калибровки тап уезжает на десятки пикселей.

2. **Чувствительность.** Порогов на самом деле два, и лёгкость нажатия
   упирается в тот, что не очевиден.

   `z1_min` отделяет касание от шума по току через панель.

   `max_resistance` — по сопротивлению между слоями: чем сильнее давят,
   тем оно меньше, и касание засчитывается, пока оно ниже порога. Именно
   этот порог и заставляет давить сильнее, если стоит слишком низким.

   Ни тот, ни другой здесь не угадываются: меряется покой, меряются ваши
   настоящие нажатия, и пороги ставятся между. Причём нарочно ближе к
   нажатию, чем к середине, — лишняя чувствительность обходится дешевле,
   чем необходимость продавливать.

Результат записывается в `app/calibration.toml` — переписывать руками
ничего не нужно. Отдельным файлом, а не в `config.toml`: тот приезжает
вместе с кодом и при следующей доставке затёр бы замеры.

Заодно сбрасывается ползунок чувствительности, если его двигали из
приложения: он лежит слоем выше и иначе отменил бы эту калибровку.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _calibration  # noqa: E402
import _service  # noqa: E402

from app import settings  # noqa: E402
from app import theme  # noqa: E402
from app import touch_calibration  # noqa: E402
from app.drivers import xpt2046  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"
# Замеры пишем отдельно: config.toml приезжает с кодом и при
# следующей доставке затёр бы их.
MEASURED = CONFIG.parent / "calibration.toml"
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


def wait_press(panel: xpt2046.Xpt2046, floor: int) -> tuple[int, int, int, float]:
    """Ждать нажатия без ограничения по времени.

    Порог здесь заведомо низкий — задача не отфильтровать, а поймать даже
    лёгкое нажатие и узнать, какой z1 оно даёт. Настоящий порог считается
    потом, по собранным числам.
    """
    while True:
        if panel._read(xpt2046.CMD_Z1) >= floor:
            peak, raw = 0, None
            # Самое слабое сопротивление за нажатие — это его самый
            # уверенный миг. По нему и судим: порог должен пропускать
            # даже такое касание, каким человек жмёт, когда не старается.
            weakest = float("inf")
            while True:
                z1 = panel._read(xpt2046.CMD_Z1)
                if z1 < floor:
                    break
                if z1 > peak:
                    peak, raw = z1, panel.raw()
                z2 = panel._read(xpt2046.CMD_Z2)
                x = panel._read(xpt2046.CMD_X)
                value = xpt2046.touch_resistance(z1, z2, x, z1_min=1)
                if value < weakest:
                    weakest = value
                time.sleep(0.02)
            time.sleep(SETTLE_S)
            if raw is not None:
                return raw[0], raw[1], peak, weakest
        time.sleep(0.02)


def write_config(values: dict[str, object]) -> None:
    """Записать замеры в calibration.toml, поверх config.toml."""
    # Ползунок чувствительности из приложения лежит слоем выше замеров и
    # молча отменил бы всё, что мы сейчас намеряли: человек увидел бы, как
    # калибровка печатает новое значение и как оно не действует. Побеждает
    # сделанное последним, а последней сейчас была калибровка.
    settings.forget(["touch.max_resistance"])
    _calibration.save(MEASURED, "touch", values, notes={
        "x_min":
            "Границы сырых значений АЦП этой панели. Подобраны\n"
            "tools/touch_calibrate.py по нажатиям в углы.",
        "max_resistance":
            "Порог силы нажатия. Сопротивление обратно силе, поэтому\n"
            "низкое значение означает «дави сильнее».",
        "z1_min":
            "Ниже этого z1 панель считается нетронутой.",
    })


def main() -> None:
    try:
        import spidev
    except ImportError:
        print("\n  нужен spidev — запускать на самом Pi\n")
        raise SystemExit(1)

    _service.require_stopped()

    cfg = load_config(CONFIG)["touch"]
    from app.display.st7796s import open_spi

    # Скорость берём из раздела экрана, а не выдумываем свой ключ:
    # display_speed_hz в конфиге нет, и значение по умолчанию совпадало с
    # настоящим по чистой случайности.
    display = open_spi(speed_hz=load_config(CONFIG).get("display", {}).get(
        "speed_hz", 16_000_000))

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
    corners: list[xpt2046.Press] = []
    forces: list[float] = []
    # Четыре угла, а не два: по двум нельзя ни отличить поворот панели от
    # зеркала, ни усреднить дрожание пальца. Раньше углов было два, и
    # расчёт по ним промахивался мимо самих крестиков на 35–43 пикселя.
    for index, (x, y) in enumerate(touch_calibration.targets()):
        caption = "жми точно в крестик" if index == 0 else f"теперь в этот · {index + 1} / 4"
        target(display, x, y, caption, "нажимай как привычно, без усилия")
        raw_x, raw_y, peak, force = wait_press(panel, floor)
        corners.append(((x, y), (raw_x, raw_y)))
        presses.append(peak)
        forces.append(force)
        print(f"  угол ({x:3}, {y:3}) -> сырые ({raw_x:4}, {raw_y:4}), z1 {peak}")

    calibration, worst = xpt2046.calibration_from_presses(corners, theme.WIDTH, theme.HEIGHT)
    print(f"  ориентация: swap={calibration.swap_xy} "
          f"invert_x={calibration.invert_x} invert_y={calibration.invert_y}, "
          f"промах по углам {worst:.0f} px")

    target(display, theme.WIDTH // 2, theme.HEIGHT // 2,
           "проверка: жми в центр", "нажимай как привычно")
    raw_x, raw_y, peak, force = wait_press(panel, floor)
    presses.append(peak)
    forces.append(force)
    got = xpt2046.to_screen(raw_x, raw_y, calibration, theme.WIDTH, theme.HEIGHT)
    error = ((got[0] - theme.WIDTH // 2) ** 2 + (got[1] - theme.HEIGHT // 2) ** 2) ** 0.5

    # Порог ставим ближе к покою, чем к нажатию: лишняя чувствительность
    # здесь дешевле, чем необходимость давить. Шум покоя уже измерен, и
    # опускаться ниже него нельзя — вернутся ложные касания.
    weakest = min(presses)
    z1_min = max(idle_peak + 2, int(idle_peak + (weakest - idle_peak) * 0.25))

    # Порог силы — выше самого слабого из нажатий, с запасом. Ложные
    # касания он не пропустит: у нетронутой панели координата X читается
    # нулём, и сопротивление при этом считается бесконечным, сколько бы
    # ни стоял порог. Так что поднимать его можно почти свободно, и
    # скупиться тут незачем — скупость и означает «дави сильнее».
    real = [f for f in forces if f != float("inf")]
    max_resistance = int(max(real) * 1.6) if real else xpt2046.MAX_RESISTANCE

    print(f"\n  проверка центра: получилось {got}, промах {error:.0f} px")
    if error > 25:
        print("  промах великоват — стоит перекалибровать, целясь точнее")

    print(f"  нажатия дали z1 от {min(presses)} до {max(presses)}")
    print(f"  порог z1_min: {z1_min}  (было {cfg.get('z1_min', '—')})")
    if real:
        print(f"  сопротивление нажатий: от {min(real):.0f} до {max(real):.0f}")
    print(f"  порог max_resistance: {max_resistance}  "
          f"(было {cfg.get('max_resistance', int(xpt2046.MAX_RESISTANCE))})")

    values = dict(calibration.to_dict())
    values["z1_min"] = z1_min
    values["max_resistance"] = max_resistance
    write_config(values)

    target(display, theme.WIDTH // 2, theme.HEIGHT // 2, "готово", "всё записано в конфиг")
    time.sleep(1.5)
    display.close()
    spi.close()

    print("\n  Записано в app/calibration.toml. Осталось перезапустить сервис:")
    print("      sudo systemctl restart desk-companion\n")


if __name__ == "__main__":
    main()
