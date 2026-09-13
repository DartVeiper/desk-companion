"""Резистивный тачскрин XPT2046 и его калибровка.

Транспорта нет — только команды, фильтрация и пересчёт координат. Значит
и калибровочная математика, и отбраковка ложных касаний проверяются
тестами до пайки.

Панель отдаёт сырые значения АЦП, не пиксели, и границы у каждого
экземпляра свои: разброс между платами больше, чем можно списать на шум.
Поэтому калибровка обязательна и делается разово (tools/touch_calibrate.py),
а результат живёт в конфиге.

Шина общая с экраном, но чип-селект свой (CE1, GPIO7) и скорость своя:
XPT2046 держит не больше ~2 МГц, тогда как экран идёт на 32. Разные
/dev/spidev0.x позволяют задать разные скорости, поэтому драться за шину
они не будут.
"""

from __future__ import annotations

from dataclasses import dataclass

# Управляющие байты: старт, канал, 12 бит, дифференциальный режим.
CMD_X = 0xD0
CMD_Y = 0x90
CMD_Z1 = 0xB0
CMD_Z2 = 0xC0

ADC_MAX = 4095

# Сколько сырых отсчётов усредняем на одно касание. Резистивная панель
# шумит, и одиночное чтение регулярно даёт выброс на пол-экрана.
SAMPLES = 5

# Выше этого сопротивления касания нет. Именно выше, а не ниже: чем
# сильнее давят, тем МЕНЬШЕ сопротивление между слоями панели. Порог
# ориентировочный — на живой панели его придётся подобрать, сняв значения
# при уверенном нажатии и при пустом экране.
MAX_RESISTANCE = 2000.0
NO_TOUCH = float("inf")

# Ниже этого z1 касания нет. Замерено на живой панели: в покое z1 держится
# в пределах 1-6, под нажатием уходит в сотни. Шестнадцать — с запасом выше
# шума и заметно ниже любого настоящего касания.
#
# Проверять именно z1 обязательно. Раньше стояло `z1 <= 0`, и это не
# срабатывало никогда: ноль панель не отдаёт, она отдаёт единицы. А формула
# ниже умножает на raw_x, который в покое равен нулю, — и сопротивление
# выходило нулевым ВСЕГДА. Условие «касание, если ниже порога» выполнялось
# на каждом опросе: экран листался сам, без единого прикосновения.
Z1_MIN = 16


@dataclass
class Calibration:
    """Границы сырых значений и ориентация.

    Значения по умолчанию — типовые для 4" модулей, но пользоваться ими
    всерьёз нельзя: разброс между экземплярами велик, нужен свой замер.
    """

    x_min: int = 300
    x_max: int = 3800
    y_min: int = 300
    y_max: int = 3800
    swap_xy: bool = True    # альбомная ориентация
    invert_x: bool = False
    invert_y: bool = True

    def to_dict(self) -> dict:
        return {
            "x_min": self.x_min, "x_max": self.x_max,
            "y_min": self.y_min, "y_max": self.y_max,
            "swap_xy": self.swap_xy,
            "invert_x": self.invert_x, "invert_y": self.invert_y,
        }


def median(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def to_screen(raw_x: int, raw_y: int, calibration: Calibration,
              width: int, height: int) -> tuple[int, int]:
    """Сырые отсчёты АЦП -> пиксели экрана, с обрезкой по краям."""
    cal = calibration
    if cal.swap_xy:
        raw_x, raw_y = raw_y, raw_x

    span_x = max(1, cal.x_max - cal.x_min)
    span_y = max(1, cal.y_max - cal.y_min)
    x = (raw_x - cal.x_min) / span_x
    y = (raw_y - cal.y_min) / span_y
    if cal.invert_x:
        x = 1.0 - x
    if cal.invert_y:
        y = 1.0 - y

    return (
        max(0, min(width - 1, round(x * (width - 1)))),
        max(0, min(height - 1, round(y * (height - 1)))),
    )


def touch_resistance(z1: int, z2: int, raw_x: int, z1_min: int = Z1_MIN) -> float:
    """Сопротивление касания по схеме из документации.

    Величина обратная силе: жмут сильно — сопротивление мало, отпустили —
    уходит в бесконечность. Нужна не ради силы как таковой, а чтобы
    отбрасывать ложные касания: без неё резистивная панель «нажимается» от
    наводок и от собственного шлейфа, и экран живёт своей жизнью.
    """
    if z1 < z1_min:
        return NO_TOUCH  # цепь фактически разомкнута — панели никто не касается
    if raw_x <= 0:
        # Формула вырождается: множитель raw_x обнуляет всё выражение, и
        # «нет касания» превратилось бы в «давят изо всех сил».
        return NO_TOUCH
    return (z2 / z1 - 1.0) * (raw_x / ADC_MAX) * 1000.0


class Xpt2046:
    """Тач поверх произвольного транспорта SPI.

    transfer(bytes) -> bytes, как spidev.xfer2. Так драйвер проверяется
    заглушкой и не тянет spidev на машину разработки.
    """

    def __init__(self, transfer, calibration: Calibration | None = None,
                 width: int = 480, height: int = 320,
                 max_resistance: float = MAX_RESISTANCE,
                 z1_min: int = Z1_MIN) -> None:
        self._transfer = transfer
        self.calibration = calibration or Calibration()
        self.width, self.height = width, height
        # Пороги у каждой панели свои, и раньше они жили только в константах
        # модуля: значения из config.toml сюда не доходили вовсе, так что
        # правка конфига не делала ничего. Теперь их задаёт вызывающий.
        self.max_resistance = max_resistance
        self.z1_min = z1_min

    def _read(self, command: int) -> int:
        # Три байта: команда, затем два байта ответа. Значимых бит 12,
        # они выровнены влево в 16-битном слове.
        response = self._transfer(bytes([command, 0x00, 0x00]))
        return ((response[1] << 8) | response[2]) >> 3

    def raw(self) -> tuple[int, int]:
        """Усреднённые сырые координаты."""
        xs = [self._read(CMD_X) for _ in range(SAMPLES)]
        ys = [self._read(CMD_Y) for _ in range(SAMPLES)]
        return median(xs), median(ys)

    def resistance(self) -> float:
        raw_x = self._read(CMD_X)
        return touch_resistance(self._read(CMD_Z1), self._read(CMD_Z2), raw_x,
                                self.z1_min)

    def position(self) -> tuple[int, int] | None:
        """Координаты касания в пикселях. None — касания нет."""
        if self.resistance() > self.max_resistance:
            return None
        raw_x, raw_y = self.raw()
        return to_screen(raw_x, raw_y, self.calibration, self.width, self.height)


def calibration_from_corners(
    top_left: tuple[int, int], bottom_right: tuple[int, int],
    swap_xy: bool = True, invert_x: bool = False, invert_y: bool = True,
) -> Calibration:
    """Собрать калибровку по двум замерам в противоположных углах.

    Разово: скрипт рисует крестик, вы жмёте, он запоминает сырые значения.
    Границы упорядочиваем сами — какой угол даст большее число, зависит от
    ориентации панели, и заставлять человека об этом думать незачем.
    """
    x_values = sorted((top_left[0], bottom_right[0]))
    y_values = sorted((top_left[1], bottom_right[1]))
    return Calibration(
        x_min=x_values[0], x_max=x_values[1],
        y_min=y_values[0], y_max=y_values[1],
        swap_xy=swap_xy, invert_x=invert_x, invert_y=invert_y,
    )
