"""Источники поверх настоящих датчиков.

Драйверы (app/drivers/*) знают протокол, эти классы знают, когда опрашивать
и что класть в состояние. Транспорт передаётся снаружи, поэтому логика
проверяется заглушками — так же, как сами драйверы.

Общий принцип: отвалившийся датчик не роняет сервис и не стирает последнее
известное значение. Часы обязаны идти, даже когда всё остальное молчит.
"""

from __future__ import annotations

from ..drivers import ld2410, scd41
from ..state import State
from .base import Source


class Scd41Source(Source):
    """CO2, температура, влажность.

    Опрашивать чаще, чем раз в 5 секунд, бессмысленно: датчик обновляет
    показания именно с таким периодом.
    """

    name = "env"
    #: Датчик обновляет показания раз в пять секунд, но фазу его цикла мы
    #: не знаем. Спрашивать ровно раз в пять секунд значит в худшем случае
    #: разминуться и показать значение на десять секунд позже, чем можно.
    #: Спрашивать «готово?» раз в секунду стоит одной короткой посылки по
    #: I2C и убирает лишние пять секунд задержки.
    interval = 1.0

    def __init__(self, sensor: scd41.Scd41, disable_asc: bool = True,
                 temperature_offset: float | None = None) -> None:
        super().__init__()
        self.sensor = sensor
        self.disable_asc = disable_asc
        self.temperature_offset = temperature_offset
        self._started = False
        self.rejected = 0  # сколько неправдоподобных замеров отброшено

    def start(self) -> None:
        """Настроить и запустить измерения.

        Порядок важен: настроечные команды датчик принимает только до
        start(), в периодическом режиме он их игнорирует. stop() в начале
        нужен на случай, если сервис перезапустился, а датчик остался
        в измерении с прошлого раза.
        """
        self.sensor.stop()
        if self.disable_asc:
            # П.6 плана: в редко проветриваемой комнате самокалибровка
            # медленно уводит ноль. persist обязателен, иначе настройка
            # не переживёт отключение питания.
            self.sensor.set_auto_calibration(False)
            self.sensor.persist()
        if self.temperature_offset is not None:
            self.sensor.set_temperature_offset(self.temperature_offset)
            self.sensor.persist()
        self.sensor.start()
        self._started = True

    def poll(self, state: State) -> bool:
        if not self._started:
            self.start()
        if not self.sensor.ready():
            return False

        measurement = self.sensor.read()
        if not measurement.plausible:
            # Первые секунды после старта датчик отдаёт нули. Показать их
            # или записать в базу — значит испортить и экран, и историю.
            self.rejected += 1
            return False

        env = state.env
        env.co2 = measurement.co2
        env.temperature = measurement.temperature
        env.humidity = measurement.humidity
        env.updated = state.now
        return True

    def close(self) -> None:
        if self._started:
            try:
                self.sensor.stop()
            except OSError:
                pass


class Ld2410Source(Source):
    """Присутствие по радару.

    Опрашиваем часто: модуль сыплет кадрами примерно десять раз в секунду,
    и накопившийся буфер лучше разбирать сразу, а не отставать от него.
    """

    name = "presence"
    #: Модуль шлёт кадры десять раз в секунду. Опрашиваем вдвое чаще:
    #: при совпадении периодов опрос и кадр расходятся по фазе, и половину
    #: времени свежий кадр ждал бы следующего круга. Пустой опрос теперь
    #: ничего не стоит — проверка in_waiting и выход.
    interval = 0.05

    def __init__(self, port, engineering: bool = False) -> None:
        super().__init__()
        self.port = port
        self.engineering = engineering
        self._buffer = b""
        #: Последний разобранный отсчёт. Отдаётся панели калибровки: без
        #: энергии по зонам подбор порогов — гадание (п.9 плана).
        self.last_report: ld2410.Report | None = None
        self.frames = 0
        self.undecoded = 0
        #: Что не удалось настроить при подъёме. Заполняет hardware.py —
        #: он владеет портом до того, как источник начнёт его читать.
        self.setup_problems: list[str] = []

    def poll(self, state: State) -> bool:
        waiting = getattr(self.port, "in_waiting", 0)
        if not waiting:
            # Раньше тут стоял read(64) вслепую, и на пустом порту он ждал
            # свой таймаут — до 200 мс с остановленным циклом. Страдал не
            # радар: это время не опрашивались тач и энкодер, и нажатие
            # «иногда не срабатывало».
            return False
        chunk = self.port.read(waiting)
        if not chunk:
            return False

        self._buffer += chunk
        frames, self._buffer = ld2410.extract_frames(self._buffer)
        if not frames:
            return False

        # Берём только последний кадр: промежуточные уже неактуальны, а
        # разбирать их все — лишняя работа на слабом процессоре.
        report = None
        for payload in frames:
            self.frames += 1
            decoded = ld2410.decode(payload)
            if decoded is None:
                self.undecoded += 1
            else:
                report = decoded
        if report is None:
            return False

        self.last_report = report
        if report.present == state.desk.presence:
            return False

        # Время удержания настраивается в самом модуле (set_max_gates),
        # поэтому мигание гасится там, а не здесь.
        state.desk.presence = report.present
        state.desk.presence_since = state.now
        return True

    def close(self) -> None:
        try:
            self.port.close()
        except (OSError, AttributeError):
            pass


class TouchSource(Source):
    """Резистивный тач: касания превращаются в жесты.

    Опрос, а не прерывание: T_IRQ экономил бы такты, но опрос двадцать раз
    в секунду на Zero 2 W стоит доли процента, зато схема проще и не зависит
    от того, разведён ли IRQ на конкретной плате.
    """

    name = "touch"
    interval = 0.05

    def __init__(self, touch, recognizer) -> None:
        super().__init__()
        self.touch = touch
        self.recognizer = recognizer
        self._down = False

    def poll(self, state: State) -> bool:
        position = self.touch.position()
        if position is None:
            if self._down:
                self.recognizer.on_up()
                self._down = False
            return False

        if self._down:
            self.recognizer.on_move(*position)
        else:
            self.recognizer.on_down(*position)
            self._down = True
        # Само касание кадр не меняет: экран перерисуется, когда Director
        # обработает событие из шины.
        return False
