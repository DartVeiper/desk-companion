"""Сборка настоящего железа по конфигу.

Единственное место, где сходятся spidev, gpiozero и pyserial. Всё остальное
работает через абстракции, поэтому импорты здесь ленивые: на машине
разработки этих модулей нет, и файл обязан оставаться импортируемым.

Каждое устройство необязательно. Если SCD41 ещё не припаян, а экран уже
работает — сервис запустится и покажет часы. Это сделано намеренно: собирать
блок вы будете по одному узлу, и на каждом шаге должно быть видно, что
предыдущие живы.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pathlib import Path

from .display.base import Display
from .drivers import scd41, xpt2046
from .radar_levels import Levels
from .inputs.events import EventBus
from .sources.base import Source


@dataclass
class Hardware:
    """Что удалось поднять. Пустые поля — устройство не отвечает."""

    display: Display | None = None
    sources: list[Source] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Энкодер не источник и не дисплей: он ничего не отдаёт по запросу, а
    # сам шлёт события в шину. Но храним его именно тут, потому что живым
    # его делает только ссылка — без неё gpiozero освободит ножки.
    encoder: object | None = None
    #: Функции «сколько миллисекунд держат прямо сейчас». Их читает
    #: главный цикл, чтобы нарисовать полосу прогресса удержания.
    hold_providers: list[Callable[[], float]] = field(default_factory=list)

    def close(self) -> None:
        for source in self.sources:
            source.close()
        if self.display is not None:
            self.display.close()
        if self.encoder is not None:
            self.encoder.close()


def open_display(cfg: dict):
    from .display.st7796s import St7796sDisplay

    import spidev
    from gpiozero import DigitalOutputDevice, PWMOutputDevice

    spi = spidev.SpiDev()
    spi.open(cfg["spi_bus"], cfg["spi_device"])
    spi.max_speed_hz = cfg["speed_hz"]
    spi.mode = 0

    dc = DigitalOutputDevice(cfg["dc"])
    reset = DigitalOutputDevice(cfg["reset"])
    led = PWMOutputDevice(cfg["backlight"])
    led.value = 1.0

    # Живыми эти три объекта остаются только за счёт лямбд ниже: они их
    # захватывают, а сами лежат в возвращаемом дисплее. Заменить лямбды на
    # прямые ссылки вроде set_dc=dc.on значит потерять reset и led —
    # gpiozero освободит ножки, и экран погаснет без единой ошибки.

    display = St7796sDisplay(
        # У красных модулей порядок цветов BGR, но встречаются и RGB —
        # у них синий покажется вместо красного.
        bgr=cfg.get("rotation_bgr", True),
        # writebytes2 сам режет большие блоки и не требует list(), в отличие
        # от writebytes — на кадре в 300 КБ разница заметная.
        write=spi.writebytes2,
        set_dc=lambda high: dc.on() if high else dc.off(),
        set_reset=lambda high: reset.on() if high else reset.off(),
        set_backlight=lambda level: setattr(led, "value", level),
    )
    display.begin()
    return display


def open_touch(cfg: dict, bus: EventBus, width: int, height: int):
    from .inputs.gestures import GestureRecognizer
    from .sources.sensors import TouchSource

    import spidev

    spi = spidev.SpiDev()
    spi.open(cfg["spi_bus"], cfg["spi_device"])
    spi.max_speed_hz = cfg["speed_hz"]
    spi.mode = 0

    calibration = xpt2046.Calibration(
        x_min=cfg["x_min"], x_max=cfg["x_max"],
        y_min=cfg["y_min"], y_max=cfg["y_max"],
        swap_xy=cfg["swap_xy"], invert_x=cfg["invert_x"], invert_y=cfg["invert_y"],
    )
    panel = xpt2046.Xpt2046(
        lambda payload: bytes(spi.xfer2(list(payload))),
        calibration, width, height,
        max_resistance=cfg["max_resistance"],
        z1_min=cfg.get("z1_min", xpt2046.Z1_MIN),
    )
    return TouchSource(panel, GestureRecognizer(bus, width))


def _add_radar(hardware: Hardware, config: dict) -> None:
    """Поднять радар и вынести его жалобы наверх.

    Молчащую команду настройки нельзя проглатывать: модуль тогда работает
    на заводских порогах, видит присутствие в пустой комнате, и понять это
    можно только удивившись поведению часов через неделю.
    """
    source = open_radar(config["radar"])
    hardware.sources.append(source)
    hardware.problems += source.setup_problems


def open_radar(cfg: dict):
    from .drivers import ld2410
    from .sources.sensors import Ld2410Source

    import serial

    port = serial.Serial(cfg["port"], cfg["baud"], timeout=0.2)
    # Копилка уровней лежит рядом с кодом, в data/. Путь задаём отсюда:
    # источник не должен знать, где на этой машине живут файлы.
    levels = Path(__file__).resolve().parent.parent / "data" / "radar_levels.json"
    # Пороги движения ближних зон — те же, что уходят в модуль. По ним
    # источник решает, было ли движение у стола.
    source = Ld2410Source(port, engineering=cfg.get("engineering", True),
                          levels_path=levels,
                          moving_thresholds=cfg.get("gate_moving"),
                          static_thresholds=cfg.get("gate_static"))
    source.levels = Levels.load(levels)
    source.setup_problems = _configure_radar(port, cfg)
    return source


def _radar_command(port, frame: bytes, timeout: float = 0.4) -> bool:
    """Отправить команду и дождаться подтверждения. False — не ответил.

    Раньше тут стояла пауза вслепую. Она плоха вдвойне: на короткой команде
    мы ждём втрое дольше нужного (двенадцать команд — полторы секунды к
    загрузке), а на неотвеченной делаем вид, что всё прошло. Модуль на
    каждую команду отвечает своим кадром, так что ждать есть чего.
    """
    from .drivers import ld2410

    port.reset_input_buffer()
    port.write(frame)
    port.flush()
    deadline = time.monotonic() + timeout
    reply = b""
    while time.monotonic() < deadline:
        reply += port.read(port.in_waiting or 32)
        if ld2410.CMD_TAIL in reply:
            return True
        time.sleep(0.005)
    return False


def _configure_radar(port, cfg: dict) -> list[str]:
    """Залить в модуль режим, пороги и дальность из конфига.

    Без этого настройки лежат в config.toml мёртвым грузом: модуль хранит
    параметры у себя, и пока их туда не отправить, он работает на заводских.
    Ровно так и вышло с инженерным режимом: в конфиге стояло engineering =
    true, панель калибровки его ждала, а включал его когда-то давно
    диагностический скрипт — и первое же отключение питания вернуло модуль
    к заводскому базовому режиму, где энергии по зонам просто нет.

    Команды принимаются только внутри «конфигурации», поэтому её открываем
    и обязательно закрываем: оставленный открытым режим блокирует поток
    данных, и радар замолкает совсем.
    """
    from .drivers import ld2410

    moving = cfg.get("gate_moving")
    static = cfg.get("gate_static")

    plan: list[tuple[str, bytes]] = []
    if "max_moving_gate" in cfg:
        plan.append(("дальность", ld2410.set_max_gates(
            int(cfg["max_moving_gate"]), int(cfg["max_static_gate"]),
            int(cfg.get("idle_seconds", 30)))))
    if moving and static:
        for gate, (m, st) in enumerate(zip(moving, static)):
            plan.append((f"зона {gate}",
                         ld2410.set_gate_sensitivity(gate, int(m), int(st))))
    plan.append(("инженерный режим",
                 ld2410.ENABLE_ENGINEERING if cfg.get("engineering", True)
                 else ld2410.DISABLE_ENGINEERING))

    problems: list[str] = []
    if not _radar_command(port, ld2410.ENABLE_CONFIG):
        # Не отвечает — возможно, завис. Перезагрузка платы его не лечит:
        # пять вольт с ножек при ней не пропадают, и залипшее состояние
        # переживает reboot. Собственный перезапуск модуля — единственное
        # «выключить и включить» без похода к розетке.
        port.write(ld2410.RESTART)
        port.flush()
        time.sleep(1.5)
        if not _radar_command(port, ld2410.ENABLE_CONFIG):
            port.reset_input_buffer()
            return ["радар не принимает команды, работает на заводских"]

    try:
        for label, frame in plan:
            if not _radar_command(port, frame):
                problems.append(f"радар не принял: {label}")
    finally:
        # Закрываем даже при сбое: модуль, оставшийся в конфигурации,
        # перестаёт слать кадры, и это выглядит как оборванный провод.
        _radar_command(port, ld2410.END_CONFIG)
        port.reset_input_buffer()
    return problems


def open_air(cfg: dict):
    from .sources.sensors import Scd41Source

    from smbus2 import SMBus, i2c_msg

    bus = SMBus(1)

    def write(address: int, payload: bytes) -> None:
        bus.i2c_rdwr(i2c_msg.write(address, payload))

    def read(address: int, count: int) -> bytes:
        message = i2c_msg.read(address, count)
        bus.i2c_rdwr(message)
        return bytes(message)

    sensor = scd41.Scd41(write, read)
    return Scd41Source(
        sensor,
        disable_asc=cfg.get("disable_asc", True),
        # Именно get(), без "or None": ноль в Python ложный, и запись
        # temperature_offset = 0.0 превращалась в None, то есть в «не
        # трогать». Датчик оставался на заводских четырёх градусах, а
        # конфиг уверял, что поправки нет. Отсутствие ключа и ноль — разные
        # вещи, и путать их нельзя нигде, где ноль осмысленное значение.
        temperature_offset=cfg.get("temperature_offset"),
    )


def open_encoder(cfg: dict, bus: EventBus):
    """Вернуть энкодер обязательно: вызывающий держит его живым.

    Выбросить возвращённое здесь значит потерять ножки — см. Encoder.pins.
    """
    from .drivers.encoder import attach

    return attach(bus, clk=cfg["clk"], dt=cfg["dt"], sw=cfg["sw"])


def _add_encoder(hardware: "Hardware", config: dict, bus: EventBus) -> None:
    encoder = open_encoder(config["encoder"], bus)
    hardware.encoder = encoder
    hardware.hold_providers.append(
        lambda: encoder.button.held_ms(time.monotonic() * 1000))


def _add_touch(hardware: "Hardware", config: dict, bus: EventBus,
               width: int, height: int) -> None:
    source = open_touch(config["touch"], bus, width, height)
    hardware.sources.append(source)
    hardware.hold_providers.append(source.recognizer.held_ms)


def build(config: dict, bus: EventBus, width: int, height: int,
          want: set[str] | None = None) -> Hardware:
    """Поднять всё, что получится. Отказ одного узла не мешает остальным.

    want — какие узлы пробовать; None означает все. Нужно при сборке по
    частям: подключили экран, проверили, поехали дальше.
    """
    hardware = Hardware()
    steps = (
        ("display", lambda: setattr(hardware, "display", open_display(config["display"]))),
        ("encoder", lambda: _add_encoder(hardware, config, bus)),
        ("touch", lambda: _add_touch(hardware, config, bus, width, height)),
        ("radar", lambda: _add_radar(hardware, config)),
        ("air", lambda: hardware.sources.append(open_air(config["air"]))),
    )

    for name, action in steps:
        if want is not None and name not in want:
            continue
        try:
            action()
        except Exception as exc:  # noqa: BLE001 — узел не обязан быть на месте
            hardware.problems.append(f"{name}: {type(exc).__name__}: {exc}")
    return hardware
