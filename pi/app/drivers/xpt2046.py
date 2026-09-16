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
        raw = self.raw_press()
        if raw is None:
            return None
        return to_screen(raw[0], raw[1], self.calibration, self.width, self.height)

    def raw_press(self) -> tuple[int, int] | None:
        """Сырые отсчёты касания, без пересчёта в пиксели. None — касания нет.

        Нужно калибровке: пересчитывать в пиксели ещё нечем, её для того и
        делают. Порог силы при этом тот же, что у обычного касания, —
        калибровать надо тем нажатием, каким человек и будет пользоваться.
        """
        if self.resistance() > self.max_resistance:
            return None
        return self.raw()


#: Нажатие при калибровке: куда целились (пиксели) и что прочла панель.
Press = tuple[tuple[int, int], tuple[int, int]]


def calibration_from_presses(presses: list[Press], width: int,
                             height: int) -> tuple[Calibration, float]:
    """Калибровка по нажатиям в четыре угла. Возвращает её и худший промах.

    Заменяет прежний расчёт по двум углам, у которого было две ошибки
    сразу, и обе незаметны, пока панель не откалибрована по-настоящему.

    Первая — оси. to_screen при swap_xy берёт экранную X из сырого Y, а
    старый расчёт клал в x_min/x_max диапазон сырого X. У панели с осями
    одной длины это не видно; у настоящей — видно сразу.

    Вторая — отступ. Крестики стоят в тридцати пикселях от края, иначе в
    них не попасть пальцем, а границы считались так, будто нажали в самый
    угол. Разметка растягивалась, и к краям промах рос.

    Вместе на модели панели это давало промах 35–43 пикселя точно по
    крестику — ровно «мелкие кнопки мажут». Здесь прямая строится по
    нажатиям и продолжается до краёв экрана.

    Ориентацию тоже берём из нажатий, а не из конфига. Между левым и правым
    верхним углом меняется только экранная X, и какой сырой канал при этом
    сдвинулся сильнее — тот её и задаёт. Знак наклона говорит, перевёрнута
    ли ось. Так калибровка чинит и перепутанную в конфиге ориентацию, а не
    закрепляет её.

    Порядок нажатий любой: углы узнаются по тому, куда целились.
    """
    if len(presses) < 4:
        raise ValueError("нужно четыре нажатия, по одному в каждый угол")

    xs = sorted({target[0] for target, _ in presses})
    ys = sorted({target[1] for target, _ in presses})
    if len(xs) < 2 or len(ys) < 2:
        raise ValueError("нажатия должны быть в разных углах")
    left, right, top, bottom = xs[0], xs[-1], ys[0], ys[-1]

    def mean_raw(where) -> tuple[float, float]:
        chosen = [raw for target, raw in presses if where(target)]
        return (sum(r[0] for r in chosen) / len(chosen),
                sum(r[1] for r in chosen) / len(chosen))

    # Среднее по двум углам на каждой стороне: так гасится и дрожание
    # пальца, и небольшой перекос панели.
    on_left = mean_raw(lambda t: t[0] == left)
    on_right = mean_raw(lambda t: t[0] == right)
    on_top = mean_raw(lambda t: t[1] == top)
    on_bottom = mean_raw(lambda t: t[1] == bottom)

    # Какой канал сильнее меняется слева направо — тот и экранная X.
    moved_x = abs(on_right[0] - on_left[0])
    moved_y = abs(on_right[1] - on_left[1])
    swap = moved_y > moved_x
    channel = 1 if swap else 0

    def axis(near: float, far: float, near_px: int, far_px: int,
             size: int) -> tuple[int, int, bool]:
        per_px = (far - near) / (far_px - near_px)
        at_zero = near - near_px * per_px
        at_edge = at_zero + (size - 1) * per_px
        low, high = sorted((at_zero, at_edge))
        # Сырое значение убывает к краю — ось перевёрнута.
        return round(low), round(high), per_px < 0

    x_min, x_max, invert_x = axis(on_left[channel], on_right[channel],
                                  left, right, width)
    other = 1 - channel
    y_min, y_max, invert_y = axis(on_top[other], on_bottom[other],
                                  top, bottom, height)

    calibration = Calibration(x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
                              swap_xy=swap, invert_x=invert_x, invert_y=invert_y)

    worst = 0.0
    for (tx, ty), (raw_x, raw_y) in presses:
        gx, gy = to_screen(raw_x, raw_y, calibration, width, height)
        worst = max(worst, ((gx - tx) ** 2 + (gy - ty) ** 2) ** 0.5)
    return calibration, worst


