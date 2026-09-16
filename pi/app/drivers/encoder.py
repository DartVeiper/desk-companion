"""Декодер энкодера KY-040 и его кнопки.

Транспорта нет — на вход подаются состояния выводов, на выход идут события
шины. Значит и антидребезг, и различение короткого/длинного нажатия
проверяются тестами без единого провода.

Про антидребезг. Наивный подход «сменился CLK — считаем щелчок» на дешёвом
KY-040 даёт прыжки на два-три шага при быстром вращении: контакты звенят.
Табличный декодер устроен иначе — он смотрит на пару (было, стало) и
недопустимые переходы просто отбрасывает, потому что дребезг как раз их и
порождает. Отдельная выдержка по времени не нужна, а значит быстрое
вращение не теряется (п.9 требует и того, и другого одновременно).

Распиновка: CLK GPIO17 (pin 11), DT GPIO27 (pin 13), SW GPIO22 (pin 15).
"""

from __future__ import annotations

from ..inputs.events import Action, EventBus

SOURCE = "encoder"

# Переход двухбитного кода Грея -> направление. Нули на местах невозможных
# переходов: там дребезг, и его надо проглотить.
_TRANSITIONS = (
    0, -1, +1, 0,
    +1, 0, 0, -1,
    -1, 0, 0, +1,
    0, +1, -1, 0,
)

# У KY-040 один щелчок — полный цикл из четырёх переходов. Но требовать
# ровно четыре нельзя: опрос выводов идёт из обработчика, и при быстром
# вращении один фронт из четырёх теряется регулярно. Поэтому щелчок
# засчитываем иначе — см. QuadratureDecoder.
STEPS_PER_DETENT = 4
MIN_STEPS = 2

# Пороги удержания. Те же, что у тача: жест обязан вести себя одинаково,
# откуда бы ни пришёл (п.9 плана — короткое и долгое должны надёжно
# различаться на практике).
HOLD_MS = 700
SETTINGS_MS = 3000
#: Дольше этого кнопку не держат — см. STUCK_MS у тача, причина та же:
#: зажатая корпусом или замкнутая кнопка держала бы полосу удержания на
#: экране бесконечно.
STUCK_MS = 8000
#: Сколько кнопка должна пробыть отпущенной без события отпускания, чтобы
#: считать это событие потерянным. С запасом выше выдержки антидребезга
#: (20 мс) и задержки обработчика: настоящее отпускание успевает прийти.
LOST_RELEASE_MS = 150


class QuadratureDecoder:
    """Пара CLK/DT -> щелчки. +1 по часовой, -1 против.

    Щелчок засчитывается по **возврату в то положение, из которого начали**,
    а не по счёту до четырёх. Причина простая: считая до четырёх, мы теряем
    щелчок каждый раз, когда пропущен хоть один фронт, — а при вращении
    рукой это происходит постоянно, и снаружи выглядит как «крутанул, а
    ничего не переключилось».

    Возврат в исходное положение — признак надёжнее: он остаётся верным и
    когда из четырёх переходов дошли три, и когда посреди щелчка звенел
    контакт. Запасной порог в четыре шага оставлен на случай, если исходное
    положение так и не увидели.
    """

    def __init__(self) -> None:
        self._prev = 0
        self._accum = 0
        self._anchor = 0  # положение, из которого начался текущий щелчок
        self.rejected = 0  # сколько недопустимых переходов проглочено

    def update(self, clk: int, dt: int) -> int:
        code = (clk << 1) | dt
        if code == self._prev:
            return 0

        direction = _TRANSITIONS[(self._prev << 2) | code]
        previous, self._prev = self._prev, code

        if direction == 0:
            # Дребезг или пропущенный фронт. Накопитель НЕ обнуляем: раньше
            # обнуляли, и один звон контакта съедал весь щелчок целиком.
            self.rejected += 1
            return 0

        if self._accum == 0:
            self._anchor = previous

        self._accum += direction

        back_home = code == self._anchor and abs(self._accum) >= MIN_STEPS
        if back_home or abs(self._accum) >= STEPS_PER_DETENT:
            step = 1 if self._accum > 0 else -1
            self._accum = 0
            return step
        return 0


class ButtonDecoder:
    """Нажатия кнопки -> TAP / HOLD / SETTINGS.

    Классифицируем ПО ОТПУСКАНИЮ, а не по достижению порога: иначе по
    дороге к трёхсекундному удержанию успел бы сработать вход в ручной
    статус на семистах миллисекундах.
    """

    def __init__(self) -> None:
        self._down_at: float | None = None
        #: Когда впервые увидели кнопку отпущенной при незакрытом нажатии.
        self._up_seen_at: float | None = None
        #: Нажатие держится дольше STUCK_MS и забыто до отпускания.
        self.stuck = False
        #: Сколько раз отпускание потерялось и нажатие сбросили по уровню.
        self.lost_releases = 0

    def press(self, timestamp_ms: float) -> None:
        self._down_at = timestamp_ms
        self._up_seen_at = None
        self.stuck = False

    def held_ms(self, now_ms: float, pressed: bool | None = None) -> float:
        """Сколько держат прямо сейчас — для индикатора прогресса.

        pressed — что показывает ножка прямо сейчас, если её можно прочитать.
        По нему ловится потерянное отпускание: gpiozero с выдержкой
        антидребезга умеет проглотить последний фронт, и тогда нажатие
        остаётся открытым навсегда, а с ним и полоса удержания на экране.
        Действие для такого нажатия не отправляем: когда кнопку отпустили на
        самом деле, неизвестно, и долгим оно могло и не быть.
        """
        if self._down_at is None:
            return 0.0
        if pressed is False:
            if self._up_seen_at is None:
                self._up_seen_at = now_ms
            elif now_ms - self._up_seen_at >= LOST_RELEASE_MS:
                self._down_at = self._up_seen_at = None
                self.lost_releases += 1
                return 0.0
        else:
            self._up_seen_at = None
        held = max(0.0, now_ms - self._down_at)
        if held >= STUCK_MS:
            self._down_at = None
            self.stuck = True
            return 0.0
        return held

    def release(self, timestamp_ms: float) -> Action | None:
        self.stuck = False
        self._up_seen_at = None
        if self._down_at is None:
            return None
        held = timestamp_ms - self._down_at
        self._down_at = None
        if held >= SETTINGS_MS:
            return Action.SETTINGS
        if held >= HOLD_MS:
            return Action.HOLD
        return Action.SELECT


class Encoder:
    """Оба декодера вместе, с публикацией в шину событий."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.rotary = QuadratureDecoder()
        self.button = ButtonDecoder()
        # Объекты gpiozero живут здесь, а не в локальных переменных attach.
        # Иначе после выхода из attach на них не остаётся ссылок, сборщик
        # мусора их уничтожает, gpiozero освобождает ножки — и обработчики
        # молча перестают вызываться. Уровни при этом читаются нормально,
        # так что снаружи это выглядит как исправное железо без событий.
        self.pins: tuple = ()
        #: Как прочитать кнопку прямо сейчас. None — нечем, только события.
        self.read_pressed = None

    def held_ms(self, now_ms: float) -> float:
        """Сколько держат кнопку — с проверкой по самой ножке."""
        pressed = None
        if self.read_pressed is not None:
            try:
                pressed = bool(self.read_pressed())
            except Exception:  # noqa: BLE001 — индикатор не повод падать
                pressed = None
        lost, stuck = self.button.lost_releases, self.button.stuck
        held = self.button.held_ms(now_ms, pressed)
        if self.button.lost_releases != lost:
            print("  энкодер: отпускание кнопки потерялось — нажатие сброшено")
        if self.button.stuck and not stuck:
            print(f"  энкодер: кнопку держат дольше {STUCK_MS // 1000} с — "
                  f"не учитываю до отпускания")
        return held

    def close(self) -> None:
        for pin in self.pins:
            pin.close()
        self.pins = ()

    def on_rotate(self, clk: int, dt: int) -> None:
        step = self.rotary.update(clk, dt)
        if step > 0:
            self.bus.emit(Action.NEXT, SOURCE)
        elif step < 0:
            self.bus.emit(Action.PREV, SOURCE)

    def on_press(self, timestamp_ms: float) -> None:
        self.button.press(timestamp_ms)

    def on_release(self, timestamp_ms: float) -> None:
        action = self.button.release(timestamp_ms)
        if action is not None:
            self.bus.emit(action, SOURCE)


def attach(bus: EventBus, clk: int = 17, dt: int = 27, sw: int = 22) -> Encoder:
    """Подключить к настоящим выводам через gpiozero.

    Импорт внутри: на машине разработки gpiozero нет, а файл должен
    оставаться импортируемым, чтобы тесты логики шли без железа.
    """
    import time

    from gpiozero import Button, DigitalInputDevice

    encoder = Encoder(bus)
    clk_pin = DigitalInputDevice(clk, pull_up=True)
    dt_pin = DigitalInputDevice(dt, pull_up=True)
    sw_pin = Button(sw, pull_up=True, bounce_time=0.02)

    def rotated() -> None:
        encoder.on_rotate(clk_pin.value, dt_pin.value)

    clk_pin.when_activated = rotated
    clk_pin.when_deactivated = rotated
    dt_pin.when_activated = rotated
    dt_pin.when_deactivated = rotated
    sw_pin.when_pressed = lambda: encoder.on_press(time.monotonic() * 1000)
    sw_pin.when_released = lambda: encoder.on_release(time.monotonic() * 1000)

    # Уровень ножки, а не последнее событие: по нему ловится потерянное
    # отпускание (см. ButtonDecoder.held_ms).
    encoder.read_pressed = lambda: sw_pin.is_pressed

    # Ровно то, ради чего заведено поле: пережить выход из этой функции.
    encoder.pins = (clk_pin, dt_pin, sw_pin)
    return encoder
