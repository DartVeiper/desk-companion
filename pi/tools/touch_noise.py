"""Сколько ложных нажатий даёт панель при разных порогах.

    sudo systemctl stop desk-companion
    python3 tools/touch_noise.py
    sudo systemctl start desk-companion

**Экрана не касаться.** Скрипт меряет покой: он смотрит, что панель
отдаёт, когда её никто не трогает, и считает, при каком пороге этот шум
начал бы выглядеть нажатием.

Зачем. Порог max_resistance отделяет нажатие от покоя, и поднимать его —
значит делать нажатие легче. В конфиге написано, что поднимать почти
безопасно: у нетронутой панели координата X читается нулём, а тогда
касания нет при любом пороге. Проверка это опровергла — на 15000 блок
залипал, потому что часть отсчётов покоя даёт ненулевой X и конечное
сопротивление.

Так что «безопасно» — свойство не порога, а конкретной панели в
конкретном корпусе. Здесь оно и меряется.

Цифра, которая нужна на выходе: **самый высокий порог, при котором ложных
нажатий ноль.** Выше него ползунок чувствительности пускать нельзя.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.drivers import xpt2046  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"

#: Пороги, которые проверяем. Верхний конец — максимум ползунка.
LADDER = (2000, 4000, 6000, 8000, 9000, 10500, 12000, 15000)

SECONDS = 20.0
RATE = 20  # столько же раз в секунду опрашивает настоящий источник тача


def main() -> int:
    try:
        import spidev
    except ImportError:
        print("\n  нужен spidev — запускать на самом Pi\n")
        return 1

    cfg = load_config(CONFIG)["touch"]

    spi = spidev.SpiDev()
    spi.open(cfg["spi_bus"], cfg["spi_device"])
    spi.max_speed_hz = cfg["speed_hz"]
    spi.mode = 0

    panel = xpt2046.Xpt2046(
        lambda payload: bytes(spi.xfer2(list(payload))),
        None, 480, 320,
        # Порог ставим заведомо бесконечный: нам нужны сами сопротивления,
        # а не чужое решение о том, касание это или нет.
        max_resistance=float("inf"),
        z1_min=cfg.get("z1_min", xpt2046.Z1_MIN),
    )

    print(f"\n  Меряю покой {SECONDS:.0f} с. ЭКРАНА НЕ КАСАТЬСЯ.\n")

    values: list[float] = []
    finite: list[float] = []
    deadline = time.monotonic() + SECONDS
    while time.monotonic() < deadline:
        r = panel.resistance()
        values.append(r)
        if r != xpt2046.NO_TOUCH:
            finite.append(r)
        time.sleep(1.0 / RATE)

    spi.close()

    total = len(values)
    print(f"  отсчётов: {total}, из них с конечным сопротивлением: {len(finite)}")
    if finite:
        finite.sort()
        print(f"  их разброс: от {min(finite):.0f} до {max(finite):.0f}, "
              f"медиана {finite[len(finite) // 2]:.0f}")
    print()

    safe = None
    for threshold in LADDER:
        false_presses = sum(1 for r in values if r <= threshold)
        share = false_presses / total * 100
        mark = "чисто" if false_presses == 0 else f"ЛОЖНЫХ {false_presses} ({share:.1f} %)"
        current = "  <- сейчас в конфиге" if threshold == int(cfg["max_resistance"]) else ""
        print(f"    {threshold:>6}   {mark}{current}")
        if false_presses == 0:
            safe = threshold

    print()
    if safe is None:
        print("  Ложные нажатия есть даже на самом низком пороге — дело не в")
        print("  пороге. Смотреть z1_min и шлейф.\n")
        return 1
    if safe == LADDER[-1]:
        print(f"  Панель чиста на всех порогах до {safe}.\n")
    else:
        print(f"  Безопасный потолок: {safe}. Выше панель начинает")
        print("  нажиматься сама, и с залипшим касанием из накладки уже")
        print("  не выйти.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
