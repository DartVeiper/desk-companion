"""Драйвер экрана ST7796S 480x320 по SPI.

Своей реализацией потому, что готовых нет: luma.lcd и Adafruit CircuitPython
RGB Display этот контроллер не поддерживают.

ВАЖНО про последовательность инициализации: она частично зависит от платы,
а не только от контроллера. Приведённая ниже — типовая для красных модулей
4" ST7796S; её почти наверняка придётся править по факту. Отлаживать удобнее
всего интерактивно, из REPL: подключиться, послать команду, посмотреть, что
на стекле. Ради этого транспорт вынесен в параметры — можно подсунуть
заглушку и проверить логику до пайки.

Распиновка (см. также раздел 10.5 плана, дополненный под этот экран):
    CS   -> GPIO8  (CE0, pin 24)      MOSI -> GPIO10 (pin 19)
    DC   -> GPIO25 (pin 22)           SCK  -> GPIO11 (pin 23)
    RST  -> GPIO24 (pin 18)           LED  -> GPIO18 (pin 12, PWM)
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .. import theme
from .banded import BandedDisplay

# Команды ST7796S
SWRESET, SLPOUT, NORON, INVOFF, DISPON = 0x01, 0x11, 0x13, 0x20, 0x29
CASET, RASET, RAMWR, MADCTL, COLMOD = 0x2A, 0x2B, 0x2C, 0x36, 0x3A
CSCON = 0xF0  # разблокировка вендорских регистров

# MADCTL: альбомная ориентация, порядок цветов BGR.
# Красные модули ST7796S почти всегда BGR; если цвета окажутся
# перепутаны местами (синий вместо красного) — снять бит 0x08.
MADCTL_LANDSCAPE = 0x28 | 0x08

INIT: tuple[tuple[int, bytes, float], ...] = (
    (SWRESET, b"", 0.150),
    (SLPOUT, b"", 0.120),
    (CSCON, b"\xc3", 0),          # разблокировать команды вендора
    (CSCON, b"\x96", 0),
    (MADCTL, bytes([MADCTL_LANDSCAPE]), 0),
    (COLMOD, b"\x55", 0),          # 16 бит на точку, RGB565
    (0xB4, b"\x01", 0),            # инверсия строк выключена
    (0xB6, b"\x80\x02\x3b", 0),    # управление дисплеем
    (0xE8, b"\x40\x8a\x00\x00\x29\x19\xa5\x33", 0),
    (0xC1, b"\x06", 0),            # питание 2
    (0xC2, b"\xa7", 0),            # питание 3
    (0xC5, b"\x18", 0.120),        # VCOM
    (0xE0, b"\xf0\x09\x0b\x06\x04\x15\x2f\x54\x42\x3c\x17\x14\x18\x1b", 0),  # гамма +
    (0xE1, b"\xf0\x09\x0b\x06\x04\x03\x2d\x43\x42\x3b\x16\x14\x17\x1b", 0),  # гамма −
    (CSCON, b"\x3c", 0),           # заблокировать обратно
    (CSCON, b"\x69", 0),
    (INVOFF, b"", 0),
    (NORON, b"", 0.010),
    (DISPON, b"", 0.120),
)


class St7796sDisplay(BandedDisplay):
    def __init__(
        self,
        write: Callable[[bytes], None],
        set_dc: Callable[[bool], None],
        set_reset: Callable[[bool], None] | None = None,
        set_backlight: Callable[[float], None] | None = None,
        width: int = theme.WIDTH,
        height: int = theme.HEIGHT,
        chunk: int = 4096,
    ) -> None:
        super().__init__()
        self.width, self.height = width, height
        self._write = write
        self._set_dc = set_dc
        self._set_reset = set_reset
        self._set_backlight = set_backlight
        # spidev на Pi не принимает произвольно большие блоки — упираемся в
        # bufsiz драйвера. Режем сами, чтобы не зависеть от настроек системы.
        self.chunk = chunk

    # ------------------------------------------------------------ примитивы

    def command(self, code: int, data: bytes = b"") -> None:
        self._set_dc(False)
        self._write(bytes([code]))
        if data:
            self.data(data)

    def data(self, payload: bytes) -> None:
        self._set_dc(True)
        for offset in range(0, len(payload), self.chunk):
            self._write(payload[offset:offset + self.chunk])

    # --------------------------------------------------------------- запуск

    def reset(self) -> None:
        if self._set_reset is None:
            return
        self._set_reset(True)
        time.sleep(0.01)
        self._set_reset(False)
        time.sleep(0.01)
        self._set_reset(True)
        time.sleep(0.150)

    def begin(self) -> None:
        self.reset()
        for code, payload, pause in INIT:
            self.command(code, payload)
            if pause:
                time.sleep(pause)
        self.invalidate()

    def backlight(self, level: float) -> None:
        """Яркость 0..1. Ночью гасим — четырёхдюймовый экран в тёмной
        комнате иначе работает лампой."""
        if self._set_backlight is not None:
            self._set_backlight(max(0.0, min(1.0, level)))

    # ------------------------------------------------------------- передача

    def write_window(self, x0: int, y0: int, x1: int, y1: int, payload: bytes) -> None:
        self.command(CASET, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.command(RASET, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.command(RAMWR)
        self.data(payload)


def open_spi(bus: int = 0, device: int = 0, speed_hz: int = 32_000_000,
             dc: int = 25, reset: int = 24, backlight: int = 18) -> St7796sDisplay:
    """Собрать драйвер поверх настоящих spidev и gpiozero.

    Импорты внутри: на машине разработки этих модулей нет, и файл должен
    оставаться импортируемым, чтобы тесты логики шли без железа.

    Скорость по умолчанию 32 МГц, а не 40: на проводах-перемычках 10 см
    сорок мегагерц держатся неустойчиво. Если картинка пойдёт помехами —
    снижать дальше.
    """
    import spidev
    from gpiozero import DigitalOutputDevice, PWMOutputDevice

    spi = spidev.SpiDev()
    spi.open(bus, device)
    spi.max_speed_hz = speed_hz
    spi.mode = 0

    dc_pin = DigitalOutputDevice(dc)
    reset_pin = DigitalOutputDevice(reset)
    led = PWMOutputDevice(backlight)
    led.value = 1.0

    display = St7796sDisplay(
        write=lambda payload: spi.writebytes2(payload),
        set_dc=lambda high: dc_pin.on() if high else dc_pin.off(),
        set_reset=lambda high: reset_pin.on() if high else reset_pin.off(),
        set_backlight=lambda level: setattr(led, "value", level),
    )
    display.begin()
    return display
