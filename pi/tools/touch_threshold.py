"""Подбор порога касания по замеру, а не на глаз.

    python3 tools/touch_threshold.py

Резистивная панель сообщает не «нажали», а сопротивление между слоями. Чем
сильнее давят, тем оно ниже. Порог `max_resistance` отделяет нажатие от
шума — и он свой у каждой панели.

Взятый наугад порог даёт ровно две беды, обе неприятные. Слишком высокий —
панель «нажимает» сама, экран листается без рук, и это выглядит как
поломка. Слишком низкий — приходится давить до скрипа.

Здесь порог не угадывается: сначала меряем покой, потом нажатие, и берём
число между двумя облаками значений.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _service  # noqa: E402

from app.drivers import xpt2046  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"
IDLE_SECONDS = 12
PRESS_SECONDS = 18


def sample(panel, seconds: float, label: str) -> list[float]:
    print(f"  {label} — {seconds:.0f} с", flush=True)
    out = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = panel.resistance()
        if value is not None and value != float("inf"):
            out.append(value)
        time.sleep(0.02)
    return out


def describe(values: list[float], label: str) -> None:
    if not values:
        print(f"    {label}: ни одного конечного значения")
        return
    values = sorted(values)
    print(f"    {label}: замеров {len(values)}, "
          f"минимум {values[0]:.0f}, медиана {statistics.median(values):.0f}, "
          f"максимум {values[-1]:.0f}")


def main() -> None:
    _service.require_stopped()

    cfg = load_config(CONFIG)["touch"]
    import spidev

    spi = spidev.SpiDev()
    spi.open(cfg["spi_bus"], cfg["spi_device"])
    spi.max_speed_hz = cfg["speed_hz"]
    spi.mode = 0
    panel = xpt2046.Xpt2046(lambda payload: bytes(spi.xfer2(list(payload))))

    print(f"\n  Текущий порог в конфиге: {cfg['max_resistance']}\n")

    print("  ЭТАП 1. Ничего не трогай — меряю покой.")
    idle = sample(panel, IDLE_SECONDS, "не касайся панели")
    describe(idle, "покой")

    print("\n  ЭТАП 2. Дави на панель и води по ней, не отрывая.")
    pressed = sample(panel, PRESS_SECONDS, "дави и води")
    describe(pressed, "нажатие")

    spi.close()
    print()

    if not pressed:
        print("  Под нажатием ничего не пришло — смотри T_DO на pin 21.\n")
        raise SystemExit(1)

    press_high = sorted(pressed)[int(len(pressed) * 0.95)]
    idle_low = min(idle) if idle else float("inf")

    if idle and idle_low <= press_high:
        print(f"  Облака пересекаются: покой опускается до {idle_low:.0f}, "
              f"нажатие поднимается до {press_high:.0f}.")
        print("  Порог всё равно ставим ниже покоя — ложные нажатия хуже,")
        print("  чем необходимость давить чуть сильнее.")
        suggested = int(idle_low * 0.6)
    elif idle:
        suggested = int((press_high + idle_low) / 2)
    else:
        # Покой не дал конечных значений — панель в покое честно молчит,
        # и порог можно ставить с хорошим запасом над нажатием.
        suggested = int(press_high * 1.5)

    print(f"\n  Порог: max_resistance = {suggested}")
    print(f"  (было {cfg['max_resistance']})\n")
    print("  Вписать в [touch] в app/config.toml и перезапустить сервис.\n")


if __name__ == "__main__":
    main()
