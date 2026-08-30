"""Сырой поток с радара — первое, что надо сделать с живым модулем.

    py tools/ld2410_sniff.py                 # разобранные отсчёты
    py tools/ld2410_sniff.py --raw           # ещё и сырые байты
    py tools/ld2410_sniff.py --engineering   # включить энергию по воротам

Раскладка полей в app/drivers/ld2410.py взята по документации, а ревизии
плат отличаются: числом ворот, наличием инженерного режима, порядком байт.
Этот скрипт показывает, что модуль отдаёт на самом деле, чтобы сверить.

Он же — рабочий инструмент калибровки по п.9 плана: видно, какая зона
дальности реагирует на вентилятор или шторы, а какая на человека.
Покачайте пустое кресло, посидите неподвижно, выйдите из зоны — и смотрите
на числа, а не гадайте.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.drivers import ld2410

BAR = "▁▂▃▄▅▆▇█"


def bar(value: int, scale: int = 100) -> str:
    """Один символ на значение 0..100 — компактнее любого числа."""
    index = min(len(BAR) - 1, int(value / max(1, scale) * (len(BAR) - 1)))
    return BAR[index]


def show(report: ld2410.Report, raw: bytes | None) -> None:
    line = (f"{report.state_name:18} "
            f"дистанция {report.distance_cm:4} см   "
            f"движение {report.moving_energy:3}  статика {report.static_energy:3}")
    if report.moving_gates:
        # Ворота примерно по 0.75 м: индекс 4 — это ~3 метра.
        line += ("   движение " + "".join(bar(v) for v in report.moving_gates)
                 + "  статика " + "".join(bar(v) for v in report.static_gates))
    if raw is not None:
        line += f"\n    сырые: {raw.hex(' ')}"
    print(line, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сырой поток LD2410")
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--baud", type=int, default=256000,
                        help="заводская скорость LD2410 именно такая, не 115200")
    parser.add_argument("--raw", action="store_true", help="печатать сырые байты кадра")
    parser.add_argument("--engineering", action="store_true",
                        help="включить инженерный режим: энергия по воротам")
    parser.add_argument("--seconds", type=float, help="остановиться через N секунд")
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("нужен pyserial:  sudo apt install python3-serial")
        raise SystemExit(1)

    port = serial.Serial(args.port, args.baud, timeout=0.5)
    print(f"\n  порт {args.port} на {args.baud} бод")

    if args.engineering:
        # Настроечные команды модуль принимает только внутри «конфигурации».
        for command in (ld2410.ENABLE_CONFIG, ld2410.ENABLE_ENGINEERING, ld2410.END_CONFIG):
            port.write(command)
            time.sleep(0.1)
        print("  инженерный режим включён")
    print("  Ctrl+C — стоп\n")

    buffer = b""
    started = time.monotonic()
    seen = 0
    try:
        while True:
            chunk = port.read(256)
            if chunk:
                buffer += chunk
                frames, buffer = ld2410.extract_frames(buffer)
                for payload in frames:
                    report = ld2410.decode(payload)
                    seen += 1
                    if report is None:
                        print(f"  НЕ РАЗОБРАН: {payload.hex(' ')}", flush=True)
                    else:
                        show(report, payload if args.raw else None)
            if args.seconds and time.monotonic() - started >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        print(f"\n  кадров получено: {seen}\n")


if __name__ == "__main__":
    main()
