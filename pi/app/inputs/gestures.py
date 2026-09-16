"""Распознавание жестов резистивного тача (XPT2046).

Резистивная панель реагирует на давление, а не на ёмкость: при быстром
скольжении пальцем контакт рвётся в середине жеста. Поэтому свайп считаем
по первой и последней точке касания, а не по треку — так порванный трек
всё равно распознаётся. Основным жестом остаётся тап, он надёжен.

Тап отдаётся наружу вместе с координатами, а не превращается тут же в
«листнуть вперёд». Решать, попал ли палец в карточку, должен экран — он
один знает свою раскладку. Навигация по третям остаётся запасным вариантом,
когда экран тап не забрал.

Пороги подобраны «на бумаге» и почти наверняка потребуют правки после
первой живой пробы. После правки прогнать inputs/test_gestures.py.
"""

from __future__ import annotations

import time

from .events import Action, EventBus

SOURCE = "touch"

# Уровни удержания. Классифицируем ПО ОТПУСКАНИЮ, а не по достижению порога:
# иначе по дороге к настройкам сработал бы вход в Manual Override.
HOLD_MS = 700       # п.9 плана: короткое и долгое должны надёжно различаться
SETTINGS_MS = 3000
TAP_MAX_MOVE = 25   # px: разброс в пределах дрожания пальца

#: Дольше этого нажатие не держат. Настройки открываются на трёх секундах,
#: и полоса удержания говорит «можно отпускать»; касание, которое не
#: отпускают восемь секунд, — это уже не палец, а что-то, давящее на
#: панель: край корпуса, натянутый шлейф. Такое касание забываем до
#: отпускания. Иначе полоса висела бы поверх притушенного экрана, пока
#: давление не пропадёт, и блок выглядел бы намертво зависшим — 17.09 его
#: так и описали: «картинка наслаивается краями на прошлую».
STUCK_MS = 8000

# Свайп
SWIPE_MIN_DX = 60    # px: короче — это тап, а не свайп
SWIPE_MAX_DY = 90    # px: вертикальный увод больше — жест не горизонтальный
SWIPE_MAX_MS = 1000  # дольше — палец лежал на экране и случайно поехал


class GestureRecognizer:
    """Превращает поток касаний в события шины.

    Драйвер тача вызывает on_down / on_move / on_up.
    """

    def __init__(self, bus: EventBus, screen_w: int) -> None:
        self._bus = bus
        self._w = screen_w
        self._start: tuple[int, int] | None = None
        self._last: tuple[int, int] | None = None
        self._t0 = 0.0
        #: Касание признано ложным (см. STUCK_MS) и ждёт отпускания.
        self.stuck = False

    def on_down(self, x: int, y: int) -> None:
        self._start = self._last = (x, y)
        self._t0 = time.monotonic()
        self.stuck = False

    def on_move(self, x: int, y: int) -> None:
        if self._start is not None:
            self._last = (x, y)
            self._expire()

    def held_ms(self) -> float:
        """Сколько держат прямо сейчас. Нужно индикатору прогресса на экране."""
        self._expire()
        return 0.0 if self._start is None else (time.monotonic() - self._t0) * 1000.0

    def cancel(self) -> None:
        """Забыть незаконченный жест, ничего не отправив в шину."""
        self._start = self._last = None

    def _expire(self) -> None:
        if self._start is not None and (time.monotonic() - self._t0) * 1000.0 >= STUCK_MS:
            self.cancel()
            self.stuck = True

    def on_up(self) -> None:
        self.stuck = False
        if self._start is None or self._last is None:
            return

        x0, y0 = self._start
        x1, y1 = self._last
        dx, dy = x1 - x0, y1 - y0
        dt_ms = (time.monotonic() - self._t0) * 1000.0
        self._start = self._last = None

        # Палец стоял на месте: тап, долгое или очень долгое нажатие
        if abs(dx) <= TAP_MAX_MOVE and abs(dy) <= TAP_MAX_MOVE:
            if dt_ms >= SETTINGS_MS:
                self._bus.emit(Action.SETTINGS, SOURCE)
            elif dt_ms >= HOLD_MS:
                self._bus.emit(Action.HOLD, SOURCE)
            else:
                self._bus.emit(Action.TAP, SOURCE, x0, y0)
            return

        # Горизонтальный свайп, без заметного вертикального увода
        if abs(dx) >= SWIPE_MIN_DX and abs(dy) <= SWIPE_MAX_DY and dt_ms <= SWIPE_MAX_MS:
            self._bus.emit(Action.NEXT if dx < 0 else Action.PREV, SOURCE)
            return

        # Остальное — недожест: диагональный увод, случайное касание корпуса,
        # палец, пролежавший на экране. Молча игнорируем: ложное листание
        # раздражает сильнее, чем непонятый жест.
