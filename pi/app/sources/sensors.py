"""Источники поверх настоящих датчиков.

Драйверы (app/drivers/*) знают протокол, эти классы знают, когда опрашивать
и что класть в состояние. Транспорт передаётся снаружи, поэтому логика
проверяется заглушками — так же, как сами драйверы.

Общий принцип: отвалившийся датчик не роняет сервис и не стирает последнее
известное значение. Часы обязаны идти, даже когда всё остальное молчит.
"""

from __future__ import annotations

import time

from pathlib import Path

from ..drivers import ld2410, scd41
from ..radar_levels import Levels
from ..state import State
from .base import Source


class Scd41Source(Source):
    """CO2, температура, влажность.

    Опрашивать чаще, чем раз в 5 секунд, бессмысленно: датчик обновляет
    показания именно с таким периодом.
    """

    #: Через сколько молчания считать датчик неисправным. Он обновляется
    #: раз в пять секунд, так что минута без единого правдоподобного замера
    #: — это уже не «не успел», а отказ.
    STALE_AFTER = 60.0

    #: Сколько датчик приходит в себя после запуска измерения.
    #:
    #: SCD41 вычитает из показаний собственный нагрев, и модель этого
    #: нагрева живёт только пока идёт измерение. После start() она
    #: начинается заново, и первые минуты датчик завышает температуру
    #: примерно на четыре градуса. Замерено на живой плате: 30,5 °C сразу
    #: после перезапуска, 26,5 через четыре минуты, дальше ровно.
    #:
    #: На экране это видно недолго и само проходит, а вот в историю такие
    #: показания попадать не должны: по ней потом будут считать аномалии.
    SETTLE_SECONDS = 240.0

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
        #: Сколько раз писали в память датчика. Должно оставаться нулём
        #: при обычных перезапусках — иначе настройка не сохраняется.
        self.persisted = 0
        self._last_good = 0.0
        self._started_at = 0.0

    def start(self) -> None:
        """Настроить и запустить измерения.

        Порядок важен: настроечные команды датчик принимает только до
        start(), в периодическом режиме он их игнорирует. stop() в начале
        нужен на случай, если сервис перезапустился, а датчик остался
        в измерении с прошлого раза.

        Настройки сперва читаются и записываются только если отличаются.
        Причин две, и обе серьёзные. Запись в энергонезависимую память
        датчика стоит восемьсот миллисекунд каждая — две штуки добавляли
        полторы секунды к каждому старту сервиса. А ресурс этой памяти
        конечен, порядка тысяч циклов: сервис перезапускается при каждой
        доставке кода, и за один вечер отладки таких перезапусков бывает
        десяток. Драйвер об этом честно предупреждает в комментарии к
        persist — и единственный, кто его звал, предупреждение
        игнорировал.
        """
        self.sensor.stop()
        if self.disable_asc:
            # П.6 плана: в редко проветриваемой комнате самокалибровка
            # медленно уводит ноль.
            self._apply(False, self.sensor.get_auto_calibration,
                        self.sensor.set_auto_calibration)
        if self.temperature_offset is not None:
            self._apply(self.temperature_offset,
                        self.sensor.get_temperature_offset,
                        self.sensor.set_temperature_offset,
                        same=lambda a, b: abs(a - b) < 0.1)
        self.sensor.start()
        self._started = True
        self._started_at = time.monotonic()

    def _apply(self, wanted, read, write, same=lambda a, b: a == b) -> bool:
        """Записать настройку, только если в датчике лежит другая."""
        try:
            if same(wanted, read()):
                return False
        except OSError:
            pass  # не прочиталось — записываем вслепую, это безопаснее
        write(wanted)
        self.sensor.persist()
        self.persisted += 1
        return True

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
        env.settling = time.monotonic() - self._started_at < self.SETTLE_SECONDS
        env.co2 = measurement.co2
        env.temperature = measurement.temperature
        env.humidity = measurement.humidity
        env.updated = state.now
        self._last_good = time.monotonic()
        return True

    @property
    def healthy(self) -> bool:
        """Датчик отвечает и говорит правдоподобное.

        Отдельно от ok, потому что самый неприятный отказ SCD41 выглядит
        как исправность: после сбоя питания он подтверждает команды по I2C
        и возвращает нули. Исключения нет, ok остаётся True, а на экране
        часами висит последнее живое значение.
        """
        if not self.ok:
            return False
        if not self._last_good:
            return True  # ещё не прогрелся, первые замеры законно пустые
        return time.monotonic() - self._last_good < self.STALE_AFTER

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
    #: Через сколько тишины считать модуль замолчавшим. Кадры идут десять
    #: раз в секунду, так что десять секунд тишины — это отказ.
    STALE_AFTER = 10.0

    #: Модуль шлёт кадры десять раз в секунду. Опрашиваем вдвое чаще:
    #: при совпадении периодов опрос и кадр расходятся по фазе, и половину
    #: времени свежий кадр ждал бы следующего круга. Пустой опрос теперь
    #: ничего не стоит — проверка in_waiting и выход.
    interval = 0.05

    #: Как часто сбрасывать накопленное на карту. Раз в полчаса — это
    #: десять килобайт, то есть ничто по сравнению с самой базой; чаще
    #: писать незачем, реже — обидно терять статистику при выключении.
    SAVE_EVERY = 1800.0

    def __init__(self, port, engineering: bool = False,
                 levels_path: Path | None = None) -> None:
        super().__init__()
        self.port = port
        self.engineering = engineering
        #: Сколько раз каждая зона показывала каждый уровень энергии.
        #: Из этой копилки калибровка потом достаёт и фон пустой комнаты,
        #: и уровень присутствия — не требуя ставить опыт.
        self.levels = Levels()
        self.levels_path = levels_path
        self._saved_at = 0.0
        self._buffer = b""
        #: Последний разобранный отсчёт. Отдаётся панели калибровки: без
        #: энергии по зонам подбор порогов — гадание (п.9 плана).
        self.last_report: ld2410.Report | None = None
        self.frames = 0
        self.undecoded = 0
        self._last_frame_at = 0.0
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
        self._last_frame_at = time.monotonic()
        if report.moving_gates:
            self.levels.add(report.moving_gates, report.static_gates)
            self._maybe_save()
        if report.present == state.desk.presence:
            return False

        # Время удержания настраивается в самом модуле (set_max_gates),
        # поэтому мигание гасится там, а не здесь.
        state.desk.presence = report.present
        state.desk.presence_since = state.now
        return True

    def _maybe_save(self) -> None:
        now = time.monotonic()
        if self.levels_path is None or now - self._saved_at < self.SAVE_EVERY:
            return
        self._saved_at = now
        try:
            self.levels.save(self.levels_path)
        except OSError:
            pass  # копилка — удобство, а не работа сервиса

    def close(self) -> None:
        if self.levels_path is not None:
            try:
                self.levels.save(self.levels_path)
            except OSError:
                pass
        try:
            self.port.close()
        except (OSError, AttributeError):
            pass

    @property
    def healthy(self) -> bool:
        """Кадры идут. Модуль умеет замолкать, оставаясь подключённым:
        например, если его оставили в режиме конфигурации."""
        if not self.ok:
            return False
        if not self._last_frame_at:
            return True
        return time.monotonic() - self._last_frame_at < self.STALE_AFTER


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
