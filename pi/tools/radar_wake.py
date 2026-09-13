"""Радар молчит — разбудить его, не вставая из-за стола.

    python3 tools/radar_wake.py

Зачем. Модуль замолкает по трём причинам, и лечатся они по-разному:

  • остался в режиме конфигурации — в нём поток данных выключен.
    Такое случается, если программу прервали между «открыть настройку»
    и «закрыть». Лечится командой закрытия;
  • завис внутри себя — лечится собственной перезагрузкой модуля;
  • не доходит питание или перепутаны провода — командами не лечится.

Главное, что стоит знать: перезагрузка платы модуль НЕ перезапускает.
Пять вольт с ножек при этом не пропадают, и залипшее состояние переживает
reboot — ровно так же, как это было с датчиком воздуха. Поэтому команда
перезапуска модуля здесь и нужна: это единственное «выключить и включить»,
доступное без похода к розетке.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _service  # noqa: E402

from app.drivers import ld2410  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"


def sniff(port, seconds: float = 2.0) -> tuple[int, int]:
    """Сколько байт и сколько разобранных кадров пришло за время."""
    raw = b""
    frames_seen = 0
    buffer = b""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunk = port.read(port.in_waiting or 64)
        raw += chunk
        buffer += chunk
        frames, buffer = ld2410.extract_frames(buffer)
        frames_seen += len(frames)
        time.sleep(0.05)
    return len(raw), frames_seen


def main() -> None:
    _service.require_stopped()

    import serial

    cfg = load_config(CONFIG)["radar"]
    port = serial.Serial(cfg["port"], cfg["baud"], timeout=0.3)
    port.reset_input_buffer()

    print(f"\n  порт {cfg['port']}, скорость {cfg['baud']}\n")

    size, frames = sniff(port)
    print(f"  как есть:          {size:5} байт, кадров {frames}")
    if frames:
        print("\n  Радар говорит, будить нечего.\n")
        port.close()
        return

    # Шаг 1. Закрыть настройку. Если модуль в ней застрял, поток данных
    # выключен, и это выглядит ровно как оборванный провод.
    port.write(ld2410.END_CONFIG)
    port.flush()
    time.sleep(0.3)
    port.reset_input_buffer()
    size, frames = sniff(port)
    print(f"  закрыл настройку:  {size:5} байт, кадров {frames}")
    if frames:
        print("\n  Разбудился. Модуль был застрял в режиме конфигурации.\n")
        port.close()
        return

    # Шаг 2. Перезапуск самого модуля — программная замена выдёргиванию
    # из розетки. Команда принимается только внутри настройки.
    port.write(ld2410.ENABLE_CONFIG)
    port.flush()
    time.sleep(0.2)
    port.write(ld2410.RESTART)
    port.flush()
    print("  перезапускаю модуль, жду 3 секунды...", flush=True)
    time.sleep(3.0)
    port.reset_input_buffer()
    size, frames = sniff(port, 3.0)
    print(f"  после перезапуска: {size:5} байт, кадров {frames}")
    if frames:
        print("\n  Разбудился перезапуском модуля.\n")
        port.close()
        return

    port.close()
    print("\n  Не отвечает.")
    if size:
        print("  Байты идут, но кадры не собираются — скорость не та.")
        print("  Заводская у LD2410 — 256000; если её меняли, смотри")
        print("  tools/ld2410_sniff.py, он перебирает скорости.")
    else:
        print("  Ни одного байта. Командами это не лечится, смотри провода:")
        print("    • TX радара → pin 10, RX радара → pin 8 (перекрещиваются)")
        print("    • питание 5 В (pin 4), от 3,3 модуль не запускается")
        print("    • земля радара — свой провод до pin 6")
        print("  Если провода на месте — снять питание с платы физически,")
        print("  выключением из розетки на минуту. Перезагрузка не поможет.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
