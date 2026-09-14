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

# MADCTL: альбомная ориентация. Бит 0x08 переключает порядок цветов на
# BGR — красные модули ST7796S почти всегда такие, но встречаются и RGB,
# и тогда синий показывается вместо красного.
#
# Значение собирается по флагу, а не зашито: в конфиге стоит rotation_bgr,
# и до этой правки он был мёртвой ручкой — и конфиг, и README советовали
# его переключить, а код его не читал. Совет, который ничего не делает,
# хуже отсутствия совета: по нему ищут не там.
#: Только ориентация: обмен строк и столбцов (бит MV). Без цвета.
#: Осторожно с арифметикой: привычное для этих модулей 0x28 — это уже
#: MV вместе с BGR, бит 0x08 в нём стоит. Сложив 0x28 с 0x08, получишь
#: то же 0x28 и решишь, что флаг работает, хотя он не менял ничего.
MADCTL_LANDSCAPE = 0x20
#: Только порядок цветов.
MADCTL_BGR = 0x08


def madctl(bgr: bool = True) -> int:
    """Значение MADCTL под нужный порядок цветов."""
    return MADCTL_LANDSCAPE | (MADCTL_BGR if bgr else 0)


INIT: tuple[tuple[int, bytes, float], ...] = (
    (SWRESET, b"", 0.150),
    (SLPOUT, b"", 0.120),
    (CSCON, b"\xc3", 0),          # разблокировать команды вендора
    (CSCON, b"\x96", 0),
    (MADCTL, bytes([madctl()]), 0),   # порядок цветов уточняется в begin()
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
        chunk: int = 0,
        bgr: bool = True,
    ) -> None:
        super().__init__()
        self.width, self.height = width, height
        #: Порядок цветов модуля. У красных плат ST7796S почти всегда BGR,
        #: но встречаются и RGB — у них синий покажется вместо красного.
        self.bgr = bgr
        self._write = write
        self._set_dc = set_dc
        self._set_reset = set_reset
        self._set_backlight = set_backlight
        #: Резать ли посылку самим. Ноль — отдавать целиком.
        #:
        #: Резали по 4096 байт, считая, что spidev не примет больше своего
        #: bufsiz. На деле writebytes2 сам разбивает буфер любого размера, и
        #: делает это в C, одним системным вызовом на всю посылку. Ручная
        #: нарезка полного кадра на семьдесят пять кусков обходилась в
        #: 262 мс против 172 мс одним вызовом — треть времени уходила на
        #: сами вызовы. Параметр остался ради заглушек в тестах и бэкендов,
        #: которые большой блок действительно не примут.
        self.chunk = chunk

    # ------------------------------------------------------------ примитивы

    def command(self, code: int, data: bytes = b"") -> None:
        self._set_dc(False)
        self._write(bytes([code]))
        if data:
            self.data(data)

    def data(self, payload: bytes) -> None:
        self._set_dc(True)
        if not self.chunk:
            self._write(payload)
            return
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
        # Порядок цветов — после общей последовательности: так значение из
        # конфига перекрывает то, что стоит в INIT по умолчанию, и правка
        # одной строки не требует трогать саму последовательность запуска.
        self.command(MADCTL, bytes([madctl(self.bgr)]))
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
