"""Пошаговая проверка железа. Запускать на Pi после каждого припаянного узла.

    py tools/bringup.py            # всё по порядку
    py tools/bringup.py display    # только экран
    py tools/bringup.py kernel i2c # несколько шагов

Смысл: собирать блок вы будете по одному узлу, и каждый должен проверяться
сразу. Если подключить всё разом и запустить сервис, отказ одного датчика
будет неотличим от ошибки в проводке другого, а искать придётся среди
двадцати проводов.

Каждый шаг при отказе печатает не «ошибка», а что именно посмотреть.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.screens.registry import load_config

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"

OK, FAIL, WARN = "\033[1;32m OK \033[0m", "\033[1;31mСБОЙ\033[0m", "\033[1;33m ?? \033[0m"
passed = failed = 0


def report(ok: bool, title: str, detail: str = "", hint: str = "") -> bool:
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    print(f"  [{OK if ok else FAIL}] {title}" + (f"  —  {detail}" if detail else ""))
    if not ok and hint:
        for line in hint.strip().splitlines():
            print(f"         {line.strip()}")
    return ok


def note(text: str) -> None:
    print(f"  [{WARN}] {text}")


def head(title: str) -> None:
    print(f"\n\033[1;36m── {title}\033[0m")


# ─────────────────────────────────────────────────────────────── интерфейсы

def step_kernel(cfg: dict) -> None:
    head("Интерфейсы ядра")
    for path, what, hint in (
        ("/dev/spidev0.0", "SPI для экрана",
         "dtparam=spi=on в /boot/firmware/config.txt, затем перезагрузка"),
        ("/dev/spidev0.1", "SPI для тача (CE1)",
         "тот же dtparam=spi=on — CE1 появляется вместе с CE0"),
        ("/dev/i2c-1", "I2C для SCD41",
         "dtparam=i2c_arm=on и модуль i2c-dev в /etc/modules"),
    ):
        report(Path(path).exists(), f"{path} — {what}", hint=hint)

    serial = Path("/dev/serial0")
    if report(serial.exists(), "/dev/serial0 — UART для радара",
              hint="enable_uart=1 в config.txt"):
        target = serial.resolve().name
        report(
            target == "ttyAMA0", f"serial0 указывает на {target}",
            detail="полноценный PL011" if target == "ttyAMA0" else "это mini-UART",
            hint="""нужен dtoverlay=disable-bt в config.txt.
                    Без него UART отдан Bluetooth, а на GPIO остаётся
                    mini-UART: его скорость привязана к частоте ядра и
                    плавает, связь с радаром будет рваться""",
        )


def step_i2c(cfg: dict) -> None:
    head("Опрос шины I2C")
    try:
        out = subprocess.run(["i2cdetect", "-y", "1"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        report(False, "i2cdetect", str(exc), "sudo apt install i2c-tools")
        return

    found = {int(token, 16) for line in out.splitlines()[1:]
             for token in line.split()[1:] if token not in ("--", "UU")}
    print("   ", " ".join(f"0x{a:02x}" for a in sorted(found)) or "пусто")

    report(0x62 in found, "SCD41 на 0x62",
           hint="""проверь SDA на GPIO2 (pin 3) и SCL на GPIO3 (pin 5),
                   питание 3.3 В с pin 1. Пустая шина целиком — почти всегда
                   перепутаны SDA и SCL""")
    if 0x3C in found:
        note("на 0x3C отвечает OLED — это нормально, адреса не конфликтуют")


# ────────────────────────────────────────────────────────────────── экран

def step_display(cfg: dict) -> None:
    head("Экран ST7796S")
    try:
        from PIL import Image, ImageDraw

        from app import theme
        from app.hardware import open_display

        display = open_display(cfg["display"])
    except Exception as exc:  # noqa: BLE001
        report(False, "инициализация", f"{type(exc).__name__}: {exc}",
               hint="""проверь DC (GPIO25, pin 22), RST (GPIO24, pin 18),
                       CS на CE0 (pin 24), MOSI (pin 19), SCK (pin 23)""")
        return

    report(True, "инициализация прошла")

    # Заливки: если цвета перепутаны местами — в конфиге неверный rotation_bgr.
    for name, color in (("красным", (255, 0, 0)), ("зелёным", (0, 255, 0)),
                        ("синим", (0, 0, 255))):
        frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), color)
        ImageDraw.Draw(frame).text((20, 20), name, font=theme.font(40, bold=True),
                                   fill=(255, 255, 255))
        display.show(frame)
        display.invalidate()  # заливка целиком, а не разница с прошлым кадром
        time.sleep(1.2)

    # Сетка по краям: проверяем, что видно все четыре угла и ничего не срезано.
    frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, theme.WIDTH - 1, theme.HEIGHT - 1), outline=(255, 255, 255), width=2)
    for x in range(0, theme.WIDTH, 60):
        draw.line((x, 0, x, theme.HEIGHT), fill=(40, 40, 40))
    for y in range(0, theme.HEIGHT, 40):
        draw.line((0, y, theme.WIDTH, y), fill=(40, 40, 40))
    draw.text((10, 8), "верх-лево", font=theme.font(20, bold=True), fill=(255, 255, 255))
    draw.text((theme.WIDTH - 10, theme.HEIGHT - 8), "низ-право",
              font=theme.font(20, bold=True), fill=(255, 255, 255), anchor="rs")
    display.show(frame)

    print("\n    Посмотри на экран и ответь:")
    answer = input("    цвета назывались правильно, рамка видна целиком? [y/n] ").strip().lower()
    report(answer.startswith(("y", "д")), "картинка верна",
           hint="""цвета перепутаны (синий вместо красного) — поставь
                   rotation_bgr = false в [display].
                   Рябь и полосы — снизь speed_hz до 16000000.
                   Белый экран — проверь DC и RST""")
    display.close()


# ──────────────────────────────────────────────────────────────────── тач

def step_touch(cfg: dict) -> None:
    head("Тач XPT2046")
    try:
        from app.hardware import open_touch
        from app.inputs.events import EventBus

        source = open_touch(cfg["touch"], EventBus(), 480, 320)
    except Exception as exc:  # noqa: BLE001
        report(False, "инициализация", f"{type(exc).__name__}: {exc}",
               hint="T_CS должен идти на CE1 (GPIO7, pin 26), T_DO на MISO (pin 21)")
        return

    print("    Жми в разные места панели 8 секунд...")
    seen, deadline = [], time.monotonic() + 8
    while time.monotonic() < deadline:
        position = source.touch.position()
        if position:
            seen.append(position)
            print(f"      касание {position}", end="\r")
        time.sleep(0.05)

    report(bool(seen), f"касаний поймано: {len(seen)}",
           hint="""молчит — проверь T_CS на pin 26 и T_DO на pin 21.
                   Если ловит без нажатий, панель «залипла» — подними
                   max_resistance в [touch]""")
    if seen:
        xs, ys = [p[0] for p in seen], [p[1] for p in seen]
        report(max(xs) - min(xs) > 50 and max(ys) - min(ys) > 50,
               f"разброс по X {max(xs) - min(xs)}, по Y {max(ys) - min(ys)}",
               hint="координаты не меняются — прогони tools/touch_calibrate.py")


# ────────────────────────────────────────────────────────────────── энкодер

def step_encoder(cfg: dict) -> None:
    head("Энкодер KY-040")
    try:
        from app.hardware import open_encoder
        from app.inputs.events import EventBus

        bus = EventBus()
        # Держим ссылку: без неё gpiozero освободит ножки сразу после
        # возврата, и шаг покажет тишину на исправном железе.
        encoder = open_encoder(cfg["encoder"], bus)
    except Exception as exc:  # noqa: BLE001
        report(False, "инициализация", f"{type(exc).__name__}: {exc}",
               hint="CLK GPIO17 (pin 11), DT GPIO27 (pin 13), SW GPIO22 (pin 15)")
        return

    print("    Покрути в обе стороны и нажми, 10 секунд...")
    events, deadline = [], time.monotonic() + 10
    while time.monotonic() < deadline:
        event = bus.poll()
        if event:
            events.append(event.action.name)
            print(f"      {event.action.name:9}", end="\r")
        time.sleep(0.01)

    kinds = set(events)
    report("NEXT" in kinds and "PREV" in kinds, f"вращение в обе стороны ({len(events)} событий)",
           detail=" ".join(sorted(kinds)) or "тишина",
           hint="""только в одну сторону — переставь CLK и DT местами.
                   Тишина — проверь питание с pin 17 (3.3 В) и общий с pin 14""")
    report("SELECT" in kinds or "HOLD" in kinds, "кнопка нажимается",
           hint="SW на GPIO22 (pin 15)")


# ────────────────────────────────────────────────────────────────── радар

def step_radar(cfg: dict) -> None:
    head("Радар LD2410")
    try:
        from app.hardware import open_radar
        from app.state import State

        source = open_radar(cfg["radar"])
    except Exception as exc:  # noqa: BLE001
        report(False, "открытие порта", f"{type(exc).__name__}: {exc}",
               hint="""TX модуля на pin 10 (RX Pi), RX модуля на pin 8 (TX Pi) —
                       линии перекрещиваются. Питание 5 В с pin 2 или 4, НЕ 3.3""")
        return

    print("    Слушаю 6 секунд, подвигайся перед модулем...")
    state = State()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        source._next_at = 0
        source.tick(state)
        time.sleep(0.1)

    report(source.frames > 0, f"кадров получено: {source.frames}",
           hint="""тишина — почти всегда перепутаны TX и RX или неверная
                   скорость: у LD2410 заводская 256000, а не 115200""")
    if source.frames:
        report(source.undecoded == 0, f"неразобранных кадров: {source.undecoded}",
               hint="""раскладка полей не совпала с документацией — сними
                       сырой поток: py tools/ld2410_sniff.py --raw""")
    if source.last_report:
        r = source.last_report
        print(f"      состояние: {r.state_name}, дистанция {r.distance_cm} см")
        if r.moving_gates:
            report(True, f"инженерный режим: {len(r.moving_gates)} ворот дальности")
        else:
            note("инженерного режима нет — панель калибровки будет пустой")
    source.close()


# ──────────────────────────────────────────────────────────────── воздух

def step_air(cfg: dict) -> None:
    head("Датчик воздуха SCD41")
    try:
        from app.hardware import open_air
        from app.state import State

        source = open_air(cfg["air"])
        source.start()
    except Exception as exc:  # noqa: BLE001
        report(False, "инициализация", f"{type(exc).__name__}: {exc}",
               hint="SDA pin 3, SCL pin 5, питание 3.3 В с pin 1")
        return

    report(True, "датчик отвечает, ASC отключён" if source.disable_asc else "датчик отвечает")
    print("    Жду первый замер, до 10 секунд...")

    state = State()
    deadline = time.monotonic() + 12
    got = False
    while time.monotonic() < deadline and not got:
        source._next_at = 0
        got = source.tick(state)
        time.sleep(0.5)

    if report(got, "замер получен",
              hint="""молчит дольше десяти секунд — датчику нужно время после
                      подачи питания, попробуй ещё раз. Постоянное молчание:
                      проверь питание 3.3 В"""):
        print(f"      CO2 {state.env.co2} ppm, {state.env.temperature} °C, "
              f"{state.env.humidity} %")
        report(400 <= state.env.co2 <= 2500, "значение правдоподобно",
               hint="""CO2 сильно за 2000 в обычной комнате — датчик ещё
                       прогревается либо ноль уехал, нужна калибровка""")
    if source.rejected:
        note(f"отброшено неправдоподобных замеров: {source.rejected} (нормально при старте)")
    source.close()


STEPS = {
    "kernel": step_kernel, "i2c": step_i2c, "display": step_display,
    "touch": step_touch, "encoder": step_encoder, "radar": step_radar, "air": step_air,
}


def main() -> None:
    wanted = sys.argv[1:] or list(STEPS)
    unknown = [name for name in wanted if name not in STEPS]
    if unknown:
        print(f"неизвестные шаги: {', '.join(unknown)}")
        print(f"доступно: {', '.join(STEPS)}")
        raise SystemExit(2)

    if set(wanted) - {"kernel", "i2c", "touch"}:
        import _service
        _service.require_stopped()

    cfg = load_config(CONFIG)
    print("\n\033[1mDesk Companion — проверка железа\033[0m")
    print(f"  шаги: {', '.join(wanted)}")

    for name in wanted:
        STEPS[name](cfg)

    print(f"\n  прошло {passed}, провалено {failed}\n")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
