"""Калибровка радара по замеру пустой комнаты.

    python3 tools/radar_calibrate.py --auto     # по накопленному, без опыта
    python3 tools/radar_calibrate.py            # опытом, минут пять
    python3 tools/radar_calibrate.py --empty    # только пустая комната
    python3 tools/radar_calibrate.py --desk     # только «я за столом», 15 с

Обычный способ — --auto. Блок круглосуточно считает, сколько раз каждая
зона показывала каждый уровень энергии, и за сутки комната сама бывает и
пустой, и занятой. В накопленном распределении обе картины уже есть:
нижние доли отвечают пустой комнате, верхние — присутствию. Никуда
выходить не надо, и одна случайность ничего не портит — в отличие от
опыта на четыре минуты, который портит один проход мимо двери.

Опыт остаётся для случая «надо прямо сейчас»: он даёт ответ за пять минут,
а копилке нужны часы.

Зачем. Заводские пороги у LD2410 нарочно щедрые, и радар «видит»
присутствие там, где его нет: стены, мебель и батарея отражают сигнал не
хуже человека. На замере с человеком за столом покой светился до седьмого
метра — это не человек, это комната.

Как лечится. Записываем пустую комнату, берём устойчивый фон по каждой
зоне и ставим порог выше него. Плюс ограничиваем дальность: если стол в
метре, реагировать на движение в четырёх метрах не нужно вовсе.

Почему два отдельных замера. Пустая комната требует, чтобы из неё вышли,
и записывается долго; «я за столом» — пятнадцать секунд не двигаясь. Ждать
обоих в одном запуске значит требовать от человека выйти ровно тогда, когда
скажут. Замер пустой комнаты запоминается в файл, и вторую половину можно
сделать когда угодно потом.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _service  # noqa: E402

from app.drivers import ld2410  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "app" / "config.toml"
PROFILE = ROOT / "data" / "radar_profile.json"
GATE_CM = 75
LEAVE_SECONDS = 30
RECORD_SECONDS = 180
DESK_SECONDS = 15

#: Запас над шумом пустой комнаты. Меньше — ложные срабатывания на сквозняк
#: и качнувшуюся штору; больше — придётся махать руками, чтобы вас заметили.
MARGIN = 12

#: Какую долю замеров считать «фоном комнаты». Остальные десять процентов —
#: это как раз хлопнувшая дверь и прошедший мимо человек.
QUIET = 0.90

#: Выше этого порог уже бесполезен: шкала энергии кончается на ста, и
#: зона с таким порогом не сработает никогда.
CEILING = 88

#: Доли распределения для расчёта по копилке.
#:
#: Первая версия брала 5% под фон и 90% под присутствие — и оказалась
#: неверной на живых данных. За восемнадцать часов человек провёл за
#: столом считанные проценты времени, поэтому девяностая доля попадала не
#: в присутствие, а всё в тот же фон: проверка сравнивала фон с фоном и
#: честно докладывала, что разницы нет.
#:
#: Правильные ориентиры такие. Комната пуста почти всегда, значит верх
#: основного распределения — это и есть потолок фона; порог ставим над
#: ним. А присутствие живёт в дальнем хвосте: энергия при человеке за
#: столом доходит до ста, и этот уровень виден на тысячной доле.
#:
#: Если человек, наоборот, сидит за столом полдня, обе доли окажутся в
#: присутствии, разница схлопнется — и это поймает проверка ниже.
BACKGROUND_SHARE, PRESENCE_SHARE = 0.90, 0.999

#: Меньше этого копилке верить рано: за час комната могла не побывать
#: пустой ни разу, и «фон» окажется человеком.
MIN_HOURS = 6.0

#: Выше этой занятости комнаты фон считать уже нечестно.
BUSY_ROOM = 0.25

#: Ворота 0 и 1 статику не отдают — так устроен модуль, у них слишком малая
#: дальность. Сидящего вплотную человека он держит воротами со второго, и
#: дальность меньше этой запрашивать бессмысленно.
MIN_STATIC_GATE = 2


def percentile(values: list[int], share: float) -> int:
    """Значение, ниже которого лежит заданная доля замеров.

    Максимум для порога не годится: одного прохода мимо двери хватает,
    чтобы испортить четыре минуты записи, а порог по нему уедет в потолок
    и радар ослепнет. Девяностая доля переживает короткую помеху и при
    этом честно отражает постоянный фон комнаты.
    """
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))
    return ordered[index]


def listen(port, seconds: float, label: str = "") -> tuple[list[list[int]], list[list[int]], int]:
    """Все замеры энергии по зонам за время наблюдения."""
    moving: list[list[int]] = [[] for _ in range(ld2410.GATES)]
    static: list[list[int]] = [[] for _ in range(ld2410.GATES)]
    frames_seen = 0
    buffer = b""
    started = time.monotonic()
    deadline = started + seconds
    shown = -1
    while time.monotonic() < deadline:
        buffer += port.read(port.in_waiting or 64)
        frames, buffer = ld2410.extract_frames(buffer)
        for frame in frames:
            report = ld2410.decode(frame)
            if report is None or not report.moving_gates:
                continue
            frames_seen += 1
            for i, value in enumerate(report.moving_gates[:ld2410.GATES]):
                moving[i].append(value)
            for i, value in enumerate(report.static_gates[:ld2410.GATES]):
                static[i].append(value)
        if label:
            left = int(deadline - time.monotonic())
            if left != shown and left % 15 == 0:
                shown = left
                print(f"    {label}: осталось {left} с", flush=True)
        time.sleep(0.05)
    return moving, static, frames_seen


def require_engineering(port) -> None:
    """Без инженерного режима энергии по зонам нет, и мерить нечего."""
    port.write(ld2410.ENABLE_CONFIG)
    port.flush()
    time.sleep(0.1)
    port.write(ld2410.ENABLE_ENGINEERING)
    port.flush()
    time.sleep(0.1)
    port.write(ld2410.END_CONFIG)
    port.flush()
    time.sleep(0.2)
    port.reset_input_buffer()


def table(empty_m, empty_s, desk_m, desk_s, gate_moving, gate_static,
          desk_gate: int, limit: int) -> None:
    print("\n  «пусто» — фон комнаты, «ты» — типичное значение за столом.")
    print("\n  зона  расстояние     пусто(дв/пок)  ты(дв/пок)   порог(дв/пок)")
    for i in range(ld2410.GATES):
        if i == desk_gate:
            mark = "  ← стол"
        elif i > limit:
            mark = "  (не смотрим)"
        else:
            mark = ""
        print(f"   {i}   {i * GATE_CM:3}-{(i + 1) * GATE_CM:3} см   "
              f"{empty_m[i]:3}/{empty_s[i]:3}        "
              f"{desk_m[i]:3}/{desk_s[i]:3}      "
              f"{gate_moving[i]:3}/{gate_static[i]:3}{mark}")


def write_config(moving: list[int], static: list[int],
                 max_moving: int, max_static: int) -> None:
    """Вписать пороги в блок [radar], не трогая остальной конфиг."""
    text = CONFIG.read_text(encoding="utf-8")
    block = (
        "# Пороги по зонам дальности, подобраны tools/radar_calibrate.py по\n"
        "# замеру пустой комнаты. Чем больше число, тем менее чувствительна\n"
        "# зона. Сто в дальних зонах означает «сюда не смотреть».\n"
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


def open_port():
    import serial

    cfg = load_config(CONFIG)["radar"]
    port = serial.Serial(cfg["port"], cfg["baud"], timeout=0.3)
    require_engineering(port)
    return port


def record_empty(port, seconds: float, wait: float) -> dict:
    if wait:
        print(f"  Теперь ВЫЙДИ из комнаты. Есть {int(wait)} секунд.", flush=True)
        time.sleep(wait)
    print(f"  записываю пустую комнату, {int(seconds) // 60} минуты...", flush=True)
    moving, static, frames = listen(port, seconds, "пустая комната")
    if not frames:
        raise SystemExit("\n  Радар молчит — записывать нечего.\n")
    profile = {
        "moving": [percentile(v, QUIET) for v in moving],
        "static": [percentile(v, QUIET) for v in static],
        "moving_max": [max(v, default=0) for v in moving],
        "static_max": [max(v, default=0) for v in static],
        "frames": frames, "seconds": seconds,
        "at": time.strftime("%d.%m %H:%M"),
    }
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"  записано, кадров {frames}\n", flush=True)
    return profile


def record_desk(port) -> tuple[list[int], list[int]]:
    print(f"  Сядь как обычно и не двигайся, {DESK_SECONDS} секунд.", flush=True)
    moving, static, frames = listen(port, DESK_SECONDS)
    if not frames:
        raise SystemExit("\n  Радар молчит — записывать нечего.\n")
    print("  записано\n", flush=True)
    # Здесь берём медиану: нужно типичное «человек сидит», а не пик от
    # того, что он потянулся за кружкой ровно в этот момент.
    return ([percentile(v, 0.5) for v in moving],
            [percentile(v, 0.5) for v in static])


def finish(desk_m: list[int], desk_s: list[int], profile: dict) -> None:
    empty_m, empty_s = profile["moving"], profile["static"]

    # Где стол: дальше всего от нуля по превышению над пустой комнатой.
    # Именно превышение, а не абсолютное значение: в зоне с сильным
    # отражением от стены энергия высока и без человека.
    lift = [desk_s[i] - empty_s[i] for i in range(ld2410.GATES)]
    desk_gate = max(range(MIN_STATIC_GATE, ld2410.GATES), key=lambda i: lift[i])
    limit = min(ld2410.GATES - 1, max(desk_gate + 1, MIN_STATIC_GATE))

    gate_moving, gate_static = [], []
    for i in range(ld2410.GATES):
        if i > limit:
            gate_moving.append(100)
            gate_static.append(100)
            continue
        gate_moving.append(min(100, empty_m[i] + MARGIN))
        gate_static.append(min(100, empty_s[i] + MARGIN))

    table(empty_m, empty_s, desk_m, desk_s, gate_moving, gate_static,
          desk_gate, limit)

    # Если фон в рабочих зонах упёрся в потолок, комната при записи не была
    # пустой — или модуль смотрит в стену. Записать такие пороги значит
    # ослепить радар молча, и потом неделю гадать, почему он «сломался».
    blind = [i for i in range(MIN_STATIC_GATE, limit + 1)
             if gate_static[i] >= CEILING or gate_moving[i] >= CEILING]
    if blind:
        print(f"\n  НЕ ЗАПИСЫВАЮ. В зонах {blind} фон почти на пределе шкалы.")
        print("  В пустой комнате столько быть не может — значит, при записи")
        print("  в комнате кто-то был. Такие пороги радар бы просто ослепили.")
        print("\n  Перезапиши фон, выйдя из комнаты по-настоящему:")
        print("      python3 tools/radar_calibrate.py --empty\n")
        raise SystemExit(1)

    # Проверка на здравый смысл: если за столом энергии не больше, чем в
    # пустой комнате, порог не поможет — радар просто не различает эти
    # состояния, и дело не в числах, а в установке модуля.
    if lift[desk_gate] < MARGIN:
        print("\n  ВНИМАНИЕ: в зоне стола пустая комната даёт почти столько же")
        print("  энергии, сколько человек. Порогом это не лечится — модуль")
        print("  стоит так, что не отличает вас от обстановки. Разверните его")
        print("  на место, где сидите, и уберите отражатели перед ним.\n")

    write_config(gate_moving, gate_static, limit, limit)
    print(f"\n  Записано в app/config.toml. Стол — зона {desk_gate}, "
          f"дальность ограничена {(limit + 1) * GATE_CM} см.")
    print("  Перезапусти сервис:  sudo systemctl restart desk-companion\n")


def from_levels() -> None:
    """Посчитать пороги по копилке, накопленной сервисом."""
    from app.radar_levels import Levels

    path = ROOT / "data" / "radar_levels.json"
    levels = Levels.load(path)
    if not levels.samples:
        raise SystemExit(
            "\n  Копилка пуста. Её наполняет сам сервис, пока работает, —\n"
            "  проверь, что радар поднялся: journalctl -u desk-companion\n")

    print(f"  копится {levels.hours:.1f} ч, замеров {levels.samples}\n")
    if levels.hours < MIN_HOURS:
        raise SystemExit(
            f"  Мало данных: нужно хотя бы {MIN_HOURS:.0f} часов.\n"
            "  За меньшее время комната могла не побывать пустой ни разу,\n"
            "  и «фоном» окажется человек за столом. Оставь блок работать\n"
            "  и вернись позже — копилка переживает перезагрузку.\n"
            "  Если нужно прямо сейчас: python3 tools/radar_calibrate.py\n")

    shares = (BACKGROUND_SHARE, PRESENCE_SHARE)
    empty_m, empty_s, busy_m, busy_s = [], [], [], []
    for gate in range(ld2410.GATES):
        q = levels.quantiles(gate, shares)
        empty_m.append(q["moving"][0])
        empty_s.append(q["static"][0])
        busy_m.append(q["moving"][1])
        busy_s.append(q["static"][1])

    # Где стол: там, где присутствие сильнее всего отличается от фона.
    # Именно разница, а не сама энергия: у зоны, смотрящей в стену, энергия
    # высока всегда, и по абсолютной величине она обгонит человека.
    spread = [busy_s[i] - empty_s[i] for i in range(ld2410.GATES)]
    desk_gate = max(range(MIN_STATIC_GATE, ld2410.GATES), key=lambda i: spread[i])
    occupancy = levels.share_above(desk_gate, "static",
                                   (empty_s[desk_gate] + busy_s[desk_gate]) // 2)
    limit = min(ld2410.GATES - 1, max(desk_gate + 1, MIN_STATIC_GATE))

    gate_moving, gate_static = [], []
    for i in range(ld2410.GATES):
        if i > limit:
            gate_moving.append(100)
            gate_static.append(100)
            continue
        gate_moving.append(min(100, empty_m[i] + MARGIN))
        gate_static.append(min(100, empty_s[i] + MARGIN))

    print(f"  за столом были примерно {occupancy * 100:.0f}% времени\n")
    if occupancy > BUSY_ROOM:
        # Фон считается по верху распределения, а верх — это человек, если
        # он был в комнате полдня. Числа тогда получатся завышенными, и
        # радар станет глуховат. Лучше сказать об этом прямо, чем молча
        # записать пороги, которые придётся потом искать глазами.
        print(f"  ВНИМАНИЕ: комната была занята больше {BUSY_ROOM * 100:.0f}% времени.")
        print("  Фон при этом считается по верху распределения, а верх — это")
        print("  вы сами, и пороги выйдут завышенными. Надёжнее пересчитать")
        print("  после дня, проведённого вне дома: копилка копится сама.\n")
    print("  зона  расстояние    фон(дв/пок)  занято(дв/пок)  порог(дв/пок)")
    for i in range(ld2410.GATES):
        if i == desk_gate:
            mark = "  ← стол"
        elif i > limit:
            mark = "  (не смотрим)"
        else:
            mark = ""
        print(f"   {i}   {i * GATE_CM:3}-{(i + 1) * GATE_CM:3} см   "
              f"{empty_m[i]:3}/{empty_s[i]:3}        "
              f"{busy_m[i]:3}/{busy_s[i]:3}        "
              f"{gate_moving[i]:3}/{gate_static[i]:3}{mark}")

    blind = [i for i in range(MIN_STATIC_GATE, limit + 1)
             if gate_static[i] >= CEILING or gate_moving[i] >= CEILING]
    if blind:
        print(f"\n  НЕ ЗАПИСЫВАЮ. В зонах {blind} даже фон почти на пределе")
        print("  шкалы. Это значит, что комната за всё время наблюдения ни")
        print("  разу не была пустой, либо радар смотрит в отражатель.\n")
        raise SystemExit(1)

    # Порог обязан лежать заметно ниже уровня присутствия, иначе он
    # никогда не сработает. Сравниваем именно с ним, а не с фоном: вопрос
    # не «отличается ли», а «дотянется ли энергия до порога».
    headroom = busy_s[desk_gate] - gate_static[desk_gate]
    if headroom < MARGIN:
        print(f"\n  ВНИМАНИЕ: в зоне стола порог {gate_static[desk_gate]} почти упирается")
        print(f"  в уровень присутствия {busy_s[desk_gate]} — запас всего {headroom}.")
        print("  Порогом это не лечится: модуль стоит так, что не различает вас")
        print("  и обстановку. Разверни его туда, где сидишь, и убери")
        print("  отражатели перед ним.\n")
        raise SystemExit(1)

    write_config(gate_moving, gate_static, limit, limit)
    print(f"\n  Записано в app/config.toml. Стол — зона {desk_gate}, "
          f"дальность ограничена {(limit + 1) * GATE_CM} см.")
    print("  Перезапусти сервис:  sudo systemctl restart desk-companion\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Калибровка радара")
    parser.add_argument("--auto", action="store_true",
                        help="посчитать по накопленному, ничего не делая руками")
    parser.add_argument("--empty", action="store_true",
                        help="только записать пустую комнату")
    parser.add_argument("--desk", action="store_true",
                        help="только замер «я за столом», взять пустую из файла")
    parser.add_argument("--seconds", type=float, default=RECORD_SECONDS,
                        help="сколько писать пустую комнату")
    parser.add_argument("--now", action="store_true",
                        help="не давать время на выход: комната уже пуста")
    args = parser.parse_args()

    if args.auto:
        # Порт не трогаем вовсе: считаем по тому, что сервис уже накопил.
        # Значит и останавливать его не надо — часы продолжают работать.
        print("\n  Калибровка радара по накопленной статистике.\n")
        from_levels()
        return

    _service.require_stopped()
    port = open_port()
    print("\n  Калибровка радара по пустой комнате.\n")

    try:
        if args.desk:
            if not PROFILE.exists():
                raise SystemExit("\n  Нет замера пустой комнаты. Сначала:\n"
                                 "      python3 tools/radar_calibrate.py --empty\n")
            profile = json.loads(PROFILE.read_text(encoding="utf-8"))
            print(f"  пустая комната из замера от {profile['at']}\n")
            desk_m, desk_s = record_desk(port)
            finish(desk_m, desk_s, profile)
            return

        if args.empty:
            record_empty(port, args.seconds, 0 if args.now else LEAVE_SECONDS)
            print("  Когда вернёшься за стол, доделай вторую половину:\n"
                  "      python3 tools/radar_calibrate.py --desk\n")
            return

        desk_m, desk_s = record_desk(port)
        profile = record_empty(port, args.seconds, LEAVE_SECONDS)
        finish(desk_m, desk_s, profile)
    finally:
        port.close()


if __name__ == "__main__":
    main()
