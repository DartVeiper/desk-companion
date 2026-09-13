"""Калибровка радара по замеру пустой комнаты.

    python3 tools/radar_calibrate.py

Запускаешь — и уходишь. Даётся полминуты на то, чтобы выйти, дальше две
минуты записи. Возвращаться до конца не нужно: результат запишется сам.

Зачем. Заводские пороги у LD2410 нарочно щедрые, и радар «видит»
присутствие там, где его нет: стены, мебель и батарея отражают сигнал не
хуже человека. На замере с человеком за столом покой светился до седьмого
метра — это не человек, это комната.

Как лечится. Записываем пустую комнату, берём максимум шума по каждой
зоне и ставим порог выше него. Плюс ограничиваем дальность: если стол в
метре, реагировать на движение в четырёх метрах не нужно вовсе.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _service  # noqa: E402

from app.drivers import ld2410  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"
GATE_CM = 75
LEAVE_SECONDS = 30
RECORD_SECONDS = 120

#: Запас над шумом пустой комнаты. Меньше — ложные срабатывания на сквозняк
#: и качнувшуюся штору; больше — придётся махать руками, чтобы вас заметили.
MARGIN = 12


def listen(port, seconds: float) -> tuple[list[int], list[int], int]:
    """Максимумы энергии по зонам за время наблюдения."""
    moving = [0] * ld2410.GATES
    static = [0] * ld2410.GATES
    frames_seen = 0
    buffer = b""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        buffer += port.read(port.in_waiting or 64)
        frames, buffer = ld2410.extract_frames(buffer)
        for frame in frames:
            report = ld2410.decode(frame)
            if report is None or not report.moving_gates:
                continue
            frames_seen += 1
            for i, value in enumerate(report.moving_gates[:ld2410.GATES]):
                moving[i] = max(moving[i], value)
            for i, value in enumerate(report.static_gates[:ld2410.GATES]):
                static[i] = max(static[i], value)
        time.sleep(0.05)
    return moving, static, frames_seen


def write_config(moving: list[int], static: list[int],
                 max_moving: int, max_static: int) -> None:
    """Вписать пороги в блок [radar], не трогая остальной конфиг."""
    text = CONFIG.read_text(encoding="utf-8")
    block = (
        "# Пороги по зонам дальности, подобраны tools/radar_calibrate.py по\n"
        "# замеру пустой комнаты. Чем больше число, тем менее чувствительна\n"
        "# зона. Ноль в moving для дальних зон означает «сюда не смотреть».\n"
        f"gate_moving = {moving}\n"
        f"gate_static = {static}\n"
        "# Дальше этих зон радар не смотрит вовсе: движение в другом конце\n"
        "# комнаты присутствием за столом не является.\n"
        f"max_moving_gate = {max_moving}\n"
        f"max_static_gate = {max_static}\n"
        "# Сколько модуль держит присутствие после пропадания сигнала.\n"
        "# Короткое значение даёт мигание у неподвижно сидящего человека.\n"
        "idle_seconds = 30\n"
    )
    for key in ("gate_moving", "gate_static", "max_moving_gate",
                "max_static_gate", "idle_seconds"):
        text = re.sub(rf"^{key}\s*=.*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^# Пороги по зонам.*?\n(?:^#.*\n)*", "", text, flags=re.MULTILINE)
    text = text.replace("[radar]\n", "[radar]\n" + block, 1)
    CONFIG.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    _service.require_stopped()

    import serial

    cfg = load_config(CONFIG)["radar"]
    port = serial.Serial(cfg["port"], cfg["baud"], timeout=0.3)
    port.reset_input_buffer()

    print("\n  Калибровка радара по пустой комнате.\n")
    print(f"  Сначала — где ты сидишь. Не двигайся, {15} секунд.", flush=True)
    present_moving, present_static, frames = listen(port, 15)
    if not frames:
        print("\n  Радар молчит — калибровать нечего.\n")
        port.close()
        raise SystemExit(1)

    desk_gate = max(
        (i for i, v in enumerate(present_static) if v >= 40),
        default=max(range(ld2410.GATES), key=lambda i: present_moving[i]),
    )
    print(f"  записано, стол примерно в зоне {desk_gate} "
          f"({desk_gate * GATE_CM}-{(desk_gate + 1) * GATE_CM} см)\n")

    print(f"  Теперь ВЫЙДИ из комнаты. Есть {LEAVE_SECONDS} секунд.", flush=True)
    time.sleep(LEAVE_SECONDS)
    print(f"  записываю пустую комнату, {RECORD_SECONDS // 60} минуты...", flush=True)
    empty_moving, empty_static, frames = listen(port, RECORD_SECONDS)
    print(f"  записано, кадров {frames}\n", flush=True)

    # Порог — над шумом пустой комнаты. Дальше стола не смотрим вовсе:
    # там пороги задираем в потолок, и зона перестаёт участвовать.
    limit = min(ld2410.GATES - 1, desk_gate + 1)
    gate_moving, gate_static = [], []
    for i in range(ld2410.GATES):
        if i > limit:
            gate_moving.append(100)
            gate_static.append(100)
            continue
        gate_moving.append(min(100, empty_moving[i] + MARGIN))
        gate_static.append(min(100, empty_static[i] + MARGIN))

    print("  зона  расстояние     пусто(дв/пок)  ты(дв/пок)   порог(дв/пок)")
    for i in range(ld2410.GATES):
        mark = "  ← стол" if i == desk_gate else ("  (не смотрим)" if i > limit else "")
        print(f"   {i}   {i * GATE_CM:3}-{(i + 1) * GATE_CM:3} см   "
              f"{empty_moving[i]:3}/{empty_static[i]:3}        "
              f"{present_moving[i]:3}/{present_static[i]:3}      "
              f"{gate_moving[i]:3}/{gate_static[i]:3}{mark}")

    # Проверка на здравый смысл: если за столом энергии не больше, чем в
    # пустой комнате, порог не поможет — радар просто не различает эти
    # состояния, и дело не в числах, а в установке модуля.
    if present_static[desk_gate] <= empty_static[desk_gate] + MARGIN:
        print("\n  ВНИМАНИЕ: в зоне стола пустая комната даёт почти столько же")
        print("  энергии, сколько человек. Порогом это не лечится — модуль")
        print("  стоит так, что не отличает вас от обстановки. Разверните его")
        print("  на место, где сидите, и уберите отражатели перед ним.\n")

    write_config(gate_moving, gate_static, limit, limit)
    port.close()

    print(f"\n  Записано в app/config.toml. Дальность ограничена зоной {limit} "
          f"({(limit + 1) * GATE_CM} см).")
    print("  Перезапусти сервис:  sudo systemctl restart desk-companion\n")


if __name__ == "__main__":
    main()
