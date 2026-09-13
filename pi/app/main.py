"""Главный цикл сервиса: то, что связывает всё остальное в работающий блок.

    py -m app.main --fake            # на машине разработки, кадры в PNG
    py -m app.main --lat 55.75 --lon 37.62

Цикл ничего не знает про конкретное железо: он дёргает источники, сливает
ввод, спрашивает у Director текущий экран и отдаёт кадр дисплею. Поэтому
подключение SCD41, LD2410 и SPI-экрана позже сводится к подмене классов,
а сам цикл не трогается.

Кадр рисуем не по таймеру, а когда есть что показать: экран сменился,
минута сменилась или источник принёс новое. На SPI это принципиально —
там каждый лишний кадр стоит десятки миллисекунд шины.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import director as director_mod  # noqa: E402
from app import theme  # noqa: E402
from app.display.base import Display  # noqa: E402
from app.display.preview import PreviewDisplay  # noqa: E402
from app.inputs.events import EventBus  # noqa: E402
from app.screens import registry as registry_mod  # noqa: E402
from app.screens import widgets  # noqa: E402
from app.sources.base import Source  # noqa: E402
from app import settings as settings_mod  # noqa: E402
from app import status as status_mod  # noqa: E402
from app.state import State  # noqa: E402
from app.db import Database  # noqa: E402

CONFIG = HERE / "config.toml"
TICK = 0.25          # шаг цикла: достаточно отзывчиво для ввода
FULL_REFRESH_EVERY = 300.0
MIN_FRAME_GAP = 0.2  # не чаще, чем успевает SPI на полном кадре

#: Как часто перерисовывать кадр целиком, не спрашивая разницу.
#: Нужно ради самолечения: частичная перерисовка шлёт только изменившиеся
#: куски, поэтому одна помеха на шине оставляет полосу висеть до тех пор,
#: пока содержимое этого места не сменится само. На часах такое место может
#: не меняться часами. Полный кадр раз в пять минут стирает след любой
#: разовой помехи и стоит по шине сущие копейки.
STATUS_EVERY = 1.0   # как часто обновлять снимок для дашборда


class Recorder:
    """Запись в базу по расписанию.

    Пишем агрегаты, а не события: п.4 плана прямо запрещает строку на
    каждое нажатие клавиши — это утопит базу и сожжёт microSD.
    """

    ENV_EVERY = timedelta(minutes=10)

    def __init__(self, storage: Database | None) -> None:
        self.storage = storage
        self._minute: datetime | None = None
        self._env_at: datetime | None = None
        self._last_event: tuple | None = None

    def tick(self, state: State) -> None:
        if self.storage is None:
            return
        self._minute_row(state)
        self._env_row(state)
        self._event_row(state)

    def _minute_row(self, state: State) -> None:
        minute = state.now.replace(second=0, microsecond=0)
        if self._minute == minute:
            return
        if self._minute is not None:  # первую неполную минуту пропускаем
            self.storage.add_activity_minute(
                self._minute, state.pc.keystrokes, state.pc.mouse_clicks,
                0, state.desk.presence, state.pc.category,
            )
        self._minute = minute

    def _env_row(self, state: State) -> None:
        if state.env.co2 is None:
            return
        if self._env_at and state.now - self._env_at < self.ENV_EVERY:
            return
        self.storage.add_env(state.now, state.env.co2,
                             state.env.temperature, state.env.humidity)
        self._env_at = state.now

    def _event_row(self, state: State) -> None:
        signature = (state.desk.presence, state.pc.active_app,
                     state.pc.category, state.desk.manual_status)
        if signature == self._last_event:
            return
        if self._last_event is not None:  # первый кадр — не событие
            self.storage.add_state_event(
                state.now, state.desk.presence, state.pc.active_app,
                state.pc.category, state.pc.audio_active, state.desk.manual_status,
            )
        self._last_event = signature


class Application:
    def __init__(self, display: Display, sources: list[Source],
                 storage: Database | None = None, config_path: Path = CONFIG,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.display = display
        self.sources = sources
        # Часы — параметр, а не datetime.now() внутри: иначе поведение
        # цикла на границе минуты и на автосбросе нечем проверить.
        self.clock = clock
        self.state = State()
        self.bus = EventBus()
        self.recorder = Recorder(storage)

        self.config_path = config_path
        self._settings_mtime = settings_mod.mtime()
        self._build_screens()

        self._running = False
        self._last_frame = 0.0
        self._last_full = 0.0
        self._last_minute: int | None = None
        #: Откуда брать «сколько держат прямо сейчас». Каждый элемент —
        #: функция, возвращающая миллисекунды. Пусто на машине разработки.
        self.hold_providers: list[Callable[[], float]] = []
        #: Энкодер, если он поднялся. Нужен ради счётчика отброшенных
        #: переходов: по нему видно, теряются ли щелчки, и это первое,
        #: что хочется знать, когда крутилка «иногда не срабатывает».
        self.encoder = None
        self._last_brightness: float | None = None
        self._last_screen: str | None = None
        self._last_status = 0.0
        self.frames = 0

    def _build_screens(self) -> None:
        """Собрать карусель и покой с учётом настроек из браузера."""
        config = settings_mod.apply(registry_mod.load_config(self.config_path))
        registry = registry_mod.ScreenRegistry(
            [registry_mod.instantiate(e) for e in config["screens"]["enabled"]],
        )
        self.director = director_mod.from_config(config, registry)

    def _reload_settings(self) -> bool:
        """Перечитать настройки, если их правили из браузера.

        Проверка по времени правки файла, а не подписка на события: раз в
        тик это один вызов stat, а зависимостей от inotify не появляется.
        """
        stamp = settings_mod.mtime()
        if stamp == self._settings_mtime:
            return False
        self._settings_mtime = stamp
        self._build_screens()
        return True

    # ---------------------------------------------------------------- шаг

    def tick(self) -> bool:
        """Один проход. True — кадр был отрисован."""
        self.state.now = self.clock()

        dirty = self._reload_settings()
        for source in self.sources:
            dirty |= source.tick(self.state)
        self._sensor_health()

        while (event := self.bus.poll()) is not None:
            self.director.handle(event, self.state)
            dirty = True

        # Полоса прогресса удержания. Значение живёт в драйверах ввода, а
        # рисует его отрисовка — связать их больше негде. Кадр при этом
        # должен обновляться, пока палец на кнопке, иначе полоса замрёт.
        held = max((provider() for provider in self.hold_providers), default=0.0)
        held_now = held if held > 0 else None
        if held_now != self.director.held_ms:
            self.director.held_ms = held_now
            dirty = True

        self.recorder.tick(self.state)
        self._publish_status()

        screen = self.director.current(self.state)
        if screen.name != self._last_screen:
            self._last_screen, dirty = screen.name, True
        if self.state.now.minute != self._last_minute:
            self._last_minute, dirty = self.state.now.minute, True

        now = time.monotonic()
        if now - self._last_full >= FULL_REFRESH_EVERY:
            self._last_full = now
            self.display.invalidate()
            dirty = True

        if not dirty or now - self._last_frame < MIN_FRAME_GAP:
            return False
        self._render(screen)
        return True

    def _publish_status(self) -> None:
        """Снимок для дашборда: здоровье блока и живые данные радара.

        В /run, то есть в оперативную память — запись раз в секунду на
        microSD была бы ровно тем износом, от которого предостерегает п.6.
        """
        now = time.monotonic()
        if now - self._last_status < STATUS_EVERY:
            return
        self._last_status = now
        try:
            status_mod.write(status_mod.snapshot(self.state, self.sources))
        except OSError:
            pass  # снимок — удобство, а не работа сервиса

    def _render(self, screen) -> None:
        frame = Image.new("RGB", (self.display.width, self.display.height), theme.BG)
        screen.render(self.state, ImageDraw.Draw(frame), frame)
        # Полоса прогресса удержания поверх любого экрана. Без неё три
        # секунды до настроек приходится отсчитывать вслепую, и промах
        # выглядит как «работает через раз».
        if self.director.held_ms:
            widgets.hold_overlay(frame, self.director.held_ms)
        self._apply_brightness()
        self.display.show(frame)

    def _apply_brightness(self) -> None:
        """Довести яркость из настроек до подсветки.

        Экран настроек менял только число в состоянии — до ШИМ оно не
        доходило, и ползунок не делал ничего.
        """
        level = max(0.0, min(1.0, self.state.brightness / 100))
        if level == self._last_brightness:
            return
        try:
            self.display.backlight(level)
        except (AttributeError, NotImplementedError):
            return  # бэкенд разработки подсветки не имеет
        self._last_brightness = level
        self._last_frame = time.monotonic()
        self.frames += 1

    def _sensor_health(self) -> None:
        """Отказ источника — в строку состояния, а не в лог, который никто
        не читает."""
        if self.encoder is not None:
            self.state.health.input_rejected = self.encoder.rotary.rejected
        for source in self.sources:
            if source.name.startswith("env"):
                self.state.health.scd41_ok = source.ok
            elif source.name.startswith("presence"):
                self.state.health.ld2410_ok = source.ok

    # --------------------------------------------------------------- цикл

    def run(self, max_seconds: float | None = None) -> None:
        self._running = True
        started = time.monotonic()
        while self._running:
            self.tick()
            if max_seconds and time.monotonic() - started >= max_seconds:
                break
            time.sleep(TICK)

    def stop(self, *_args) -> None:
        self._running = False

    def attach(self, display: Display | None, sources: list[Source],
               hold_providers: list[Callable[[], float]] | None = None,
               encoder=None) -> None:
        """Подключить железо после создания приложения.

        Порядок такой, потому что энкодеру и тачу нужна шина событий, а она
        живёт внутри Application. Собирать железо до него — значит городить
        шину снаружи и передавать её в двух местах.
        """
        if display is not None:
            self.display = display
        self.sources += sources
        self.hold_providers += hold_providers or []
        if encoder is not None:
            self.encoder = encoder

    def close(self) -> None:
        for source in self.sources:
            source.close()
        self.display.close()


def location(args) -> tuple[float | None, float | None]:
    """Координаты для погоды: аргументы важнее конфига.

    Конфиг нужен сервису автозапуска: зашивать координаты в юнит systemd
    значит держать их в двух местах и однажды поправить не то.
    """
    if args.lat is not None and args.lon is not None:
        return args.lat, args.lon
    try:
        from app.screens.registry import load_config
        block = load_config(CONFIG).get("location", {})
        return block.get("lat"), block.get("lon")
    except Exception:  # noqa: BLE001
        return None, None


def build_sources(args, storage: Database | None) -> list[Source]:
    from app import sources as src

    out: list[Source] = [src.SystemHealthSource(), src.ManualResetSource()]
    lat, lon = location(args)
    if lat is not None and lon is not None:
        out.append(src.WeatherSource(lat, lon))
    if storage is not None:
        out.append(src.StreakSource(storage))
        out.append(src.AnomalySource(storage))
    if args.mqtt:
        from app.sources.mqtt import MqttSource
        out.append(MqttSource(args.mqtt))
    if args.fake:
        # Поддельный ПК только когда настоящего нет: иначе он затирал бы
        # данные, пришедшие по MQTT.
        out += [src.FakePresenceSource(), src.FakeEnvSource()]
        if not args.mqtt:
            out.append(src.FakePcSource())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Desk Companion — сервис экрана")
    parser.add_argument("--fake", action="store_true",
                        help="поддельные датчики: сервис оживает без железа")
    parser.add_argument("--lat", type=float, help="широта для погоды")
    parser.add_argument("--lon", type=float, help="долгота для погоды")
    parser.add_argument("--real", action="store_true",
                        help="поднять настоящее железо по конфигу: экран, энкодер, тач, датчики")
    parser.add_argument("--mqtt", default="", metavar="HOST",
                        help="слушать брокер (обычно localhost) — данные ПК-агента")
    parser.add_argument("--db", default="", help="путь к базе; пусто — не писать")
    parser.add_argument("--frame", default="preview/live.png",
                        help="куда класть кадр (бэкенд разработки)")
    parser.add_argument("--seconds", type=float, help="остановиться через N секунд")
    args = parser.parse_args()

    storage = None
    if args.db:
        storage = Database(args.db)

    app = Application(PreviewDisplay(HERE.parent / args.frame), build_sources(args, storage), storage)
    signal.signal(signal.SIGINT, app.stop)

    if args.real:
        from app import hardware
        from app.screens.registry import load_config

        kit = hardware.build(load_config(CONFIG), app.bus,
                             app.display.width, app.display.height)
        app.attach(kit.display, kit.sources, kit.hold_providers, kit.encoder)
        for problem in kit.problems:
            # Не падаем: собирать блок вы будете по узлам, и на каждом шаге
            # должно быть видно, что уже работает, а что ещё нет.
            print(f"  не поднялось — {problem}")

    print("\n  Desk Companion — сервис")
    print(f"  режим     : {'железо' if args.real else 'разработка, кадры в PNG'}")
    print(f"  источники : {', '.join(s.name for s in app.sources)}")
    print(f"  экранов   : {len(app.director.registry.screens)}")
    if not args.real:
        print(f"  кадр      : {args.frame}")
    print(f"  база      : {args.db or 'не пишем'}")
    print("\n  Ctrl+C — стоп\n")

    try:
        app.run(args.seconds)
    finally:
        app.close()
        print(f"\n  кадров отрисовано: {app.frames}\n")


if __name__ == "__main__":
    main()
