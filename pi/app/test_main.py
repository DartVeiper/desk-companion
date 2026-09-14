"""Тесты сервисного цикла и источников. Сеть не нужна: py app/test_main.py"""

from __future__ import annotations

import sys
import time
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import Application, Recorder
from app.display.preview import PreviewDisplay
from app.sources import ManualResetSource, mqtt
from app.sources.base import Source
from app.sources.weather import HORIZON_MINUTES, RAIN_MM, rain_in_minutes
from app.state import State
from app import settings as settings_mod
from app import status as status_mod
from app.db import Database
from app.drivers import ld2410, scd41
from app.inputs.events import Action, EventBus
from app.inputs.gestures import GestureRecognizer
from app import hardware
from app import theme
from app.screens import clock as clock_mod
from app import radar_levels
from app.sources.sensors import Ld2410Source, Scd41Source, TouchSource

failed = 0


def check(name: str, got, expected) -> None:
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name:46} {got}")


def buckets(now: datetime, values: list[float], step: int = 15) -> dict:
    return {
        "time": [(now + timedelta(minutes=step * i)).isoformat(timespec="minutes")
                 for i in range(len(values))],
        "precipitation": values,
    }


print("Прогноз дождя")
now = datetime(2026, 8, 19, 12, 0)
check("сухо", rain_in_minutes(buckets(now, [0, 0, 0, 0]), now), None)
check("дождь во втором интервале", rain_in_minutes(buckets(now, [0, 0.8, 0]), now), 15)
check("дождь в четвёртом", rain_in_minutes(buckets(now, [0, 0, 0, 1.2]), now), 45)
check(f"морось ниже порога {RAIN_MM}", rain_in_minutes(buckets(now, [0.05, 0.1]), now), None)
check("нет данных", rain_in_minutes(None, now), None)
check("пустые значения", rain_in_minutes(buckets(now, [None, None]), now), None)
far = buckets(now, [0] * 8 + [3.0])
check(f"дальше {HORIZON_MINUTES} мин — не новость", rain_in_minutes(far, now), None)

print("\nОтступ после отказа источника")


class Flaky(Source):
    name = "flaky"
    interval = 900.0
    retry_after = 10.0

    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self.left = fail_times
        self.calls = 0

    def poll(self, state: State) -> bool:
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError("сеть моргнула")
        return True


s = State()
flaky = Flaky(fail_times=3)
flaky.tick(s)
check("первый отказ пойман, сервис жив", (flaky.ok, flaky.failures), (False, 1))
check("повтор не через interval, а через retry_after", round(flaky._backoff), 10)
flaky._next_at = 0
flaky.tick(s)
check("отступ удвоился", round(flaky._backoff), 20)
flaky._next_at = 0
flaky.tick(s)
check("и ещё раз", round(flaky._backoff), 40)
flaky._next_at = 0
flaky.tick(s)
check("успех сбрасывает счётчик", (flaky.ok, flaky.failures, flaky._backoff), (True, 0, 0.0))

never = Flaky(fail_times=99)
never.retry_after, never.interval = 400.0, 500.0
never.tick(s)
never._next_at = 0
never.tick(s)
check("отступ не перерастает interval", never._backoff, 500.0)

print("\nЗапись в базу")
with tempfile.TemporaryDirectory() as tmp:
    db = Database(Path(tmp) / "r.db")
    rec = Recorder(db)
    st = State(now=datetime(2026, 8, 19, 10, 0, 5))
    st.desk.presence = True
    st.pc.category = "code"

    rec.tick(st)
    check("первая неполная минута не пишется",
              db.conn.execute("SELECT COUNT(*) c FROM activity_minute").fetchone()["c"], 0)
    check("замер воздуха без CO2 не пишется",
          len(db.env_between(datetime(2026, 8, 19), datetime(2026, 8, 20))), 0)

    st.env.co2, st.env.temperature, st.env.humidity = 700, 22.5, 44.0
    rec.tick(st)
    check("замер воздуха записан", len(db.env_between(datetime(2026, 8, 19), datetime(2026, 8, 20))), 1)

    st.now = datetime(2026, 8, 19, 10, 1, 5)
    rec.tick(st)
    check("минута закрылась — строка появилась",
              db.conn.execute("SELECT COUNT(*) c FROM activity_minute").fetchone()["c"], 1)
    check("следующий замер ждёт 10 минут",
          len(db.env_between(datetime(2026, 8, 19), datetime(2026, 8, 20))), 1)

    st.now = datetime(2026, 8, 19, 10, 12, 0)
    rec.tick(st)
    check("через 10 минут записан", len(db.env_between(datetime(2026, 8, 19), datetime(2026, 8, 20))), 2)

    def events() -> int:
        return db.conn.execute("SELECT COUNT(*) c FROM state_events").fetchone()["c"]

    # Событие записывается не сразу: состояние должно продержаться. Без
    # выдержки каждое переключение окна писало строку, и за четыре часа их
    # набегало двести тридцать при восемнадцати сменах присутствия.
    changed_at = datetime(2026, 8, 19, 10, 12, 30)
    st.now = changed_at
    st.desk.presence = False
    rec.tick(st)
    check("сразу после смены — ещё не событие", events(), 0)

    st.now = changed_at + Recorder.SETTLE - timedelta(seconds=1)
    rec.tick(st)
    check("не выдержало — всё ещё не событие", events(), 0)

    st.now = changed_at + Recorder.SETTLE
    rec.tick(st)
    check("выдержало — событие записано", events(), 1)
    check("время события — когда состояние началось, а не когда поверили",
          db.conn.execute("SELECT ts FROM state_events").fetchone()["ts"],
          changed_at.isoformat(sep=" "))

    st.now += timedelta(seconds=30)
    rec.tick(st)
    check("то же состояние второй раз — не событие", events(), 1)

    # Мелькнувшее окно не должно оставить следа: вернулись к прежнему
    # состоянию раньше, чем оно выдержалось.
    st.now += timedelta(seconds=1)
    st.pc.active_app = "explorer"
    rec.tick(st)
    st.now += timedelta(seconds=2)
    st.pc.active_app = ""
    rec.tick(st)
    st.now += timedelta(seconds=30)
    rec.tick(st)
    check("мелькнувшее окно следа не оставило", events(), 1)
    # Windows не отдаёт файл, пока соединение живо, и временный каталог
    # не удаляется. На Linux бы прошло молча — тем важнее закрыть явно.
    db.close()

print("\nАвтосброс ручного статуса")


class Clock:
    """Подменяемые часы: настоящее время в тестах цикла бесполезно."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


def force_tick(app: Application) -> bool:
    """Шаг с принудительным опросом источников.

    Источники планируются по монотонным часам — так и нужно, иначе прыжок
    времени по NTP застопорил бы их все (у Pi нет RTC). Но тест двигает
    поддельные настенные часы, а монотонные при этом почти стоят, поэтому
    расписание приходится сбрасывать руками.
    """
    for source in app.sources:
        source._next_at = 0.0
    app._last_frame = 0.0
    return app.tick()


clock = Clock(datetime(2026, 8, 19, 10, 0))
app = Application(PreviewDisplay(), sources=[ManualResetSource()], clock=clock)
force_tick(app)
app.state.desk.manual_status = "не беспокоить"
app.state.desk.manual_until = datetime(2026, 8, 19, 15, 0)

clock.advance(hours=4)
force_tick(app)
check("до срока держится", app.state.desk.manual_status, "не беспокоить")

clock.advance(hours=1, seconds=1)
check("сброс помечает кадр к перерисовке", force_tick(app), True)
check("срок вышел — статус снят", app.state.desk.manual_status, None)
check("таймер снят", app.state.desk.manual_until, None)

print("\nЦикл")
clock = Clock(datetime(2026, 8, 19, 10, 0, 0))
app = Application(PreviewDisplay(), sources=[], clock=clock)
check("первый шаг рисует кадр", app.tick(), True)
app._last_frame = 0
check("та же минута, ничего не изменилось — не рисуем", app.tick(), False)
clock.advance(minutes=1)
app._last_frame = 0
check("сменилась минута — рисуем", app.tick(), True)
clock.advance(seconds=10)
check("пауза между кадрами соблюдается", app.tick(), False)

print("\nMQTT: разбор сообщений из п.5")
mst = State(now=datetime(2026, 8, 19, 12, 0))
check("heartbeat отмечает ПК живым",
      (mqtt.apply_message(mst, mqtt.HEARTBEAT, "1755600000"), mst.pc_online), (True, True))
check("активное окно", (mqtt.apply_message(mst, mqtt.ACTIVE_APP,
      '{"app":"rider64.exe","category":"code"}'), mst.pc.active_app), (True, "rider64.exe"))
check("категория", mst.pc.category, "code")
check("агрегат ввода", (mqtt.apply_message(mst, mqtt.ACTIVITY,
      '{"keys":142,"clicks":38,"mouse_px":8420}'), mst.pc.keystrokes), (True, 142))
check("звук включился",
      (mqtt.apply_message(mst, mqtt.AUDIO, "1"), mst.pc.audio_active), (True, True))
check("тот же звук второй раз — не изменение",
      mqtt.apply_message(mst, mqtt.AUDIO, "1"), False)
check("железо", (mqtt.apply_message(mst, mqtt.HARDWARE,
      '{"gpu_temp":68,"gpu_load":92,"cpu_temp":54,"cpu_load":31}'), mst.pc.gpu_load), (True, 92.0))
check("аномалия", (mqtt.apply_message(mst, mqtt.ANOMALY,
      '{"flag":true,"reason":"сессия 5 ч без перерыва"}'), mst.pc.anomaly_flag), (True, True))
check("причина аномалии", mst.pc.anomaly_reason, "сессия 5 ч без перерыва")

check("битый JSON не роняет сервис", mqtt.apply_message(mst, mqtt.ACTIVITY, "{не json"), False)
check("после битого прежнее значение цело", mst.pc.keystrokes, 142)
check("пустая строка не роняет", mqtt.apply_message(mst, mqtt.HARDWARE, ""), False)
check("чужой топик игнорируется", mqtt.apply_message(mst, "home/whatever", "1"), False)
check("null в железе допустим",
      (mqtt.apply_message(mst, mqtt.HARDWARE, '{"gpu_temp":null,"gpu_load":50}'),
       mst.pc.gpu_temp), (True, None))

print("\nMQTT: молчание агента ловится heartbeat")
silent = State(now=datetime(2026, 8, 19, 12, 0))
mqtt.apply_message(silent, mqtt.HEARTBEAT, "x")
check("сразу после heartbeat — онлайн", silent.pc_online, True)
silent.now = datetime(2026, 8, 19, 12, 1)
check("через минуту ещё онлайн", silent.pc_online, True)
silent.now = datetime(2026, 8, 19, 12, 5)
check("через пять минут — офлайн", silent.pc_online, False)

print("\nИсточник SCD41 поверх заглушки шины")


class FakeI2c:
    """Отвечает заранее заготовленными словами, запоминает команды."""

    def __init__(self, co2: int = 700, ready: bool = True) -> None:
        self.written: list[int] = []
        self.co2, self.ready_flag = co2, ready
        self.asc = True      # заводское значение: самокалибровка включена
        self.offset = 4.0    # и заводская поправка на нагрев

    def write(self, address: int, payload: bytes) -> None:
        self.written.append(int.from_bytes(payload[:2], "big"))

    def read(self, address: int, count: int) -> bytes:
        def word(value: int) -> bytes:
            raw = value.to_bytes(2, "big")
            return raw + bytes([scd41.crc8(raw)])

        if count == 3:
            # На вопрос о настройке отвечаем настройкой, на остальные —
            # признаком готовности замера. Различаем по последней команде:
            # у датчика один канал и на всё один ответ.
            last = self.written[-1] if self.written else 0
            if last == scd41.GET_ASC:
                return word(1 if self.asc else 0)
            if last == scd41.GET_TEMPERATURE_OFFSET:
                return word(int(self.offset * 65535 / 175))
            return word(0x8007 if self.ready_flag else 0x8000)
        return word(self.co2) + word(25000) + word(30000)


bus_stub = FakeI2c()
env_source = Scd41Source(scd41.Scd41(bus_stub.write, bus_stub.read, sleep=lambda _s: None))
env_state = State(now=datetime(2026, 8, 19, 12, 0))
check("первый опрос применён", env_source.tick(env_state), True)
check("CO2 в состоянии", env_state.env.co2, 700)
# Порядок принципиален: настроечные команды датчик принимает только до
# start(), в периодическом режиме молча игнорирует.
check("порядок настройки: стоп, спросить, записать, сохранить, старт",
      [hex(c) for c in bus_stub.written[:5]],
      [hex(scd41.STOP_PERIODIC), hex(scd41.GET_ASC), hex(scd41.SET_ASC),
       hex(scd41.PERSIST_SETTINGS), hex(scd41.START_PERIODIC)])
check("записали один раз", bus_stub.written.count(scd41.PERSIST_SETTINGS), 1)

# Настройка уже стоит как надо — писать нечего. Это не про лишний вызов:
# запись в память датчика стоит 800 мс и тратит её ресурс, а сервис
# перезапускается при каждой доставке кода.
settled = FakeI2c()
settled.asc = False
quiet = Scd41Source(scd41.Scd41(settled.write, settled.read, sleep=lambda _s: None))
quiet.tick(State(now=datetime(2026, 8, 19, 12, 0)))
check("настройка на месте — в память не пишем", quiet.persisted, 0)
check("и команды записи не было",
      scd41.SET_ASC in settled.written, False)
check("но измерение всё равно запущено",
      scd41.START_PERIODIC in settled.written, True)

not_ready = FakeI2c(ready=False)
lazy = Scd41Source(scd41.Scd41(not_ready.write, not_ready.read, sleep=lambda _s: None))
lazy._next_at = 0
check("замер не готов — состояние не трогаем", lazy.tick(State()), False)

zero = FakeI2c(co2=0)
warmup = Scd41Source(scd41.Scd41(zero.write, zero.read, sleep=lambda _s: None))
warmup_state = State()
warmup_state.env.co2 = 650
warmup._next_at = 0
check("нулевой CO2 при прогреве отброшен", warmup.tick(warmup_state), False)
check("прежнее значение не затёрто", warmup_state.env.co2, 650)
check("отбраковка посчитана", warmup.rejected, 1)

print("\nИсточник LD2410 поверх заглушки порта")


class FakePort:
    def __init__(self, stream: bytes) -> None:
        self.stream, self.written = stream, b""
        self.in_waiting = len(stream)

    def read(self, count: int) -> bytes:
        chunk, self.stream = self.stream[:count], self.stream[count:]
        self.in_waiting = len(self.stream)
        return chunk

    def write(self, payload: bytes) -> None:
        self.written += payload

    def close(self) -> None:
        pass


def radar_frame(state_code: int, distance: int = 150) -> bytes:
    payload = (bytes([ld2410.BASIC, 0xAA, state_code])
               + distance.to_bytes(2, "little") + bytes([60])
               + distance.to_bytes(2, "little") + bytes([50])
               + distance.to_bytes(2, "little") + bytes([0x55, 0x00]))
    return ld2410.DATA_HEAD + len(payload).to_bytes(2, "little") + payload + ld2410.DATA_TAIL


radar_state = State(now=datetime(2026, 8, 19, 12, 0))
radar = Ld2410Source(FakePort(radar_frame(ld2410.MOVING)))
check("присутствие появилось", radar.tick(radar_state), True)
check("состояние выставлено", radar_state.desk.presence, True)

radar.port = FakePort(radar_frame(ld2410.STATIC))
radar._next_at = 0
check("статичное присутствие не считается уходом", radar.tick(radar_state), False)
check("человек всё ещё за столом", radar_state.desk.presence, True)

radar.port = FakePort(radar_frame(ld2410.NO_TARGET))
radar._next_at = 0
check("ушёл", radar.tick(radar_state), True)
check("присутствия нет", radar_state.desk.presence, False)

# Кадр, разрезанный между двумя чтениями, должен склеиться.
whole = radar_frame(ld2410.MOVING)
split = Ld2410Source(FakePort(whole[:7]))
split_state = State(now=datetime(2026, 8, 19, 12, 0))
check("половина кадра — ещё нечего разбирать", split.tick(split_state), False)
split.port = FakePort(whole[7:])
split._next_at = 0
check("вторая половина склеилась", split.tick(split_state), True)


# Настройка модуля. Проверяем именно тот путь, которым идёт сервис: раньше
# у источника был свой метод configure(), его никто не звал, а инженерный
# режим в модуль не попадал вовсе — панель калибровки оставалась пустой.


class AckPort(FakePort):
    """Порт, отвечающий на команду подтверждением, как настоящий модуль."""

    def __init__(self, deaf_after: int = 99) -> None:
        super().__init__(b"")
        self.commands: list[bytes] = []
        self.deaf_after = deaf_after

    def write(self, payload: bytes) -> None:
        self.written += payload
        self.commands.append(payload)
        if len(self.commands) <= self.deaf_after:
            self.stream += (ld2410.CMD_HEAD + b"\x04\x00"
                            + b"\x00\x00\x00\x00" + ld2410.CMD_TAIL)
            self.in_waiting = len(self.stream)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.stream, self.in_waiting = b"", 0


def word_of(frame: bytes) -> int:
    """Какая команда лежит в кадре."""
    return int.from_bytes(frame[6:8], "little")


plain = AckPort()
check("без калибровки настройка не молчит",
      hardware._configure_radar(plain, {"engineering": True}), [])
words = [word_of(f) for f in plain.commands]
check("инженерный режим доехал до модуля", 0x0062 in words, True)
check("настройка закрыта", words[-1], 0x00FE)

calibrated = AckPort()
hardware._configure_radar(calibrated, {
    "engineering": True, "max_moving_gate": 3, "max_static_gate": 3,
    "gate_moving": [20] * 9, "gate_static": [30] * 9,
})
words = [word_of(f) for f in calibrated.commands]
check("пороги всех девяти зон отправлены", words.count(0x0064), 9)
check("дальность отправлена", 0x0060 in words, True)

# Модуль, замолчавший на середине: жаловаться обязаны, но настройку всё
# равно закрыть — иначе модуль останется в ней и перестанет слать кадры.
half_deaf = AckPort(deaf_after=1)
problems = hardware._configure_radar(half_deaf, {"engineering": True})
check("о непринятой команде сказано", len(problems) > 0, True)
check("настройка закрыта даже при сбое",
      word_of(half_deaf.commands[-1]), 0x00FE)

# Молчаливый отказ. Самый неприятный вид поломки: датчик отвечает по шине,
# но говорит чушь. Исключения нет, ok остаётся True, и экран часами держит
# последнее живое значение, уверяя, что всё в порядке. Именно так SCD41 и
# ведёт себя после сбоя питания.


class DeadSensor:
    """SCD41, который принимает команды и отдаёт нули."""

    def stop(self):
        pass

    def start(self):
        pass

    def set_auto_calibration(self, enabled):
        pass

    def set_temperature_offset(self, value):
        pass

    def persist(self):
        pass

    def ready(self):
        return True

    def read(self):
        return scd41.Measurement(co2=0, temperature=0.0, humidity=0.0)


dead = Scd41Source(DeadSensor(), disable_asc=False)
dead_state = State(now=datetime(2026, 8, 19, 12, 0))
check("нули не попадают в состояние", dead.tick(dead_state), False)
check("прежнее значение не испорчено", dead_state.env.co2, None)
check("пока прогревается, не жалуемся", dead.healthy, True)
dead._last_good = time.monotonic() - Scd41Source.STALE_AFTER - 1
check("минута нулей — это отказ", dead.healthy, False)
check("при этом исключения не было", dead.ok, True)

silent = Ld2410Source(FakePort(b""))
silent.tick(State(now=datetime(2026, 8, 19, 12, 0)))
check("радар без кадров сначала не жалуется", silent.healthy, True)
silent._last_frame_at = time.monotonic() - Ld2410Source.STALE_AFTER - 1
check("десять секунд тишины — это отказ", silent.healthy, False)

# Копилка уровней радара. Из неё калибровка достаёт и фон пустой комнаты,
# и уровень присутствия — вместо опыта с выходом из комнаты на пять минут.

levels = radar_levels.Levels()
# Сутки в миниатюре: треть времени человек за столом, две трети комната
# пуста. Ровно та картина, ради которой копилка и заведена.
for _ in range(200):
    levels.add([90, 80, 70] + [10] * 6, [0, 0, 85, 70] + [30] * 5)
for _ in range(400):
    levels.add([8] * 9, [0, 0, 12, 11] + [10] * 5)

near = levels.quantiles(2, (0.05, 0.90))
check("нижняя доля — фон пустой комнаты", near["static"][0] < 20, True)
check("верхняя доля — присутствие", near["static"][1] > 60, True)
check("часы считаются по числу кадров", round(levels.hours, 3),
      round(600 / 10 / 3600, 3))

# Пустая зона не должна выдумывать уровни.
empty = radar_levels.Levels()
check("без замеров доля равна нулю", empty.quantiles(0, (0.5,))["moving"], [0])

with tempfile.TemporaryDirectory() as tmp:
    saved = Path(tmp) / "levels.json"
    levels.save(saved)
    restored = radar_levels.Levels.load(saved)
    check("копилка читается обратно", restored.samples, levels.samples)
    check("и распределение то же", restored.quantiles(2, (0.9,)),
          levels.quantiles(2, (0.9,)))
    (Path(tmp) / "битый.json").write_text("{не json", encoding="utf-8")
    check("битый файл не роняет, а даёт пустую",
          radar_levels.Levels.load(Path(tmp) / "битый.json").samples, 0)

# Сколько человек сидит без перерыва. Считается отдельно от presence_since:
# тот отвечает на вопрос «когда радар в последний раз передумал» и
# сбрасывается от минутной отлучки к принтеру.

from app.stats import BREAK_MINUTES, WINDOW_HOURS  # noqa: E402

sat = State(now=datetime(2026, 9, 14, 10, 0))
start = sat.now
check("пришёл — это изменение", sat.desk.note_presence(True, start, BREAK_MINUTES), True)
check("то же присутствие второй раз — нет",
      sat.desk.note_presence(True, start, BREAK_MINUTES), False)

sat.now = start + timedelta(minutes=95)
check("сидит полтора часа", sat.desk.sitting_minutes(sat.now), 95)

# Отлучка короче перерыва счёт не сбрасывает.
sat.desk.note_presence(False, start + timedelta(minutes=95), BREAK_MINUTES)
sat.desk.note_presence(True, start + timedelta(minutes=97), BREAK_MINUTES)
sat.now = start + timedelta(minutes=100)
check("вышел на две минуты — счёт продолжился",
      sat.desk.sitting_minutes(sat.now), 100)

# Настоящий перерыв — сбрасывает.
sat.desk.note_presence(False, start + timedelta(minutes=100), BREAK_MINUTES)
sat.desk.note_presence(True, start + timedelta(minutes=100 + BREAK_MINUTES),
                       BREAK_MINUTES)
sat.now = start + timedelta(minutes=110)
check("перерыв в пять минут — счёт с нуля",
      sat.desk.sitting_minutes(sat.now), 5)   # вернулся на 105-й, сейчас 110-я

check("ушёл — счёт остановлен",
      (sat.desk.note_presence(False, sat.now, BREAK_MINUTES),
       sat.desk.sitting_minutes(sat.now)), (True, 0))

# Заголовок часов предупреждает, а не просто сообщает.
warn = State(now=datetime(2026, 9, 14, 10, 0))
warn.desk.note_presence(True, warn.now, BREAK_MINUTES)
check("час за столом — обычный статус", clock_mod.status(warn)[0], "за столом")
warn.now += timedelta(hours=WINDOW_HOURS)
check("два часа — предупреждение", clock_mod.status(warn)[0],
      f"{WINDOW_HOURS} ч без перерыва")
check("и цвет тревожный", clock_mod.status(warn)[1], theme.WARN)

# Текущий трек. Топик приходит с признаком «сохранять», чтобы после
# перезапуска блока музыка появилась сразу, не дожидаясь следующей песни.
# Обратная сторона — то же сообщение переживает и смерть агента.

media_state = State(now=datetime(2026, 8, 19, 12, 0))
media_state.pc.last_heartbeat = media_state.now
check("трек разобран",
      mqtt.apply_message(media_state, mqtt.MEDIA,
                         '{"artist": "Kai Angel", "title": "andy warhol", '
                         '"playing": true}'),
      True)
check("собран одной строкой", media_state.now_playing, "Kai Angel — andy warhol")
check("тот же трек второй раз ничего не меняет",
      mqtt.apply_message(media_state, mqtt.MEDIA,
                         '{"artist": "Kai Angel", "title": "andy warhol", '
                         '"playing": true}'),
      False)

mqtt.apply_message(media_state, mqtt.MEDIA, '{"title": "только название", "playing": true}')
check("без исполнителя — одно название", media_state.now_playing, "только название")

mqtt.apply_message(media_state, mqtt.MEDIA,
                   '{"artist": "Kai Angel", "title": "andy warhol", "playing": false}')
check("на паузе не показываем", media_state.now_playing, "")

mqtt.apply_message(media_state, mqtt.MEDIA,
                   '{"artist": "Kai Angel", "title": "andy warhol", "playing": true}')
media_state.pc.last_heartbeat = media_state.now - timedelta(minutes=10)
check("ПК пропал — трек тоже", media_state.now_playing, "")

check("мусор в топике не роняет",
      mqtt.apply_message(media_state, mqtt.MEDIA, "не json"), False)

print("\nИсточник тача")


class FakePanel:
    def __init__(self, positions: list) -> None:
        self.positions = positions

    def position(self):
        return self.positions.pop(0) if self.positions else None


touch_bus = EventBus()
recognizer = GestureRecognizer(touch_bus, 480)
touch_source = TouchSource(FakePanel([(400, 160), (300, 160), (120, 165), None]), recognizer)
touch_state = State()
for _ in range(4):
    touch_source._next_at = 0
    touch_source.tick(touch_state)
event = touch_bus.poll()
check("протяжка по панели стала свайпом", event.action if event else None, Action.NEXT)

tap_bus = EventBus()
tap_source = TouchSource(FakePanel([(240, 160), None]), GestureRecognizer(tap_bus, 480))
for _ in range(2):
    tap_source._next_at = 0
    tap_source.tick(State())
tap_event = tap_bus.poll()
check("одиночное касание стало тапом", tap_event.action if tap_event else None, Action.TAP)
check("координаты доехали", (tap_event.x, tap_event.y), (240, 160))

print("\nНастройки поверх конфига")
with tempfile.TemporaryDirectory() as tmp:
    store = Path(tmp) / "settings.json"

    check("без файла — только значения по умолчанию",
          settings_mod.load(store), dict(settings_mod.DEFAULTS))

    saved = settings_mod.save({"ambient.away_delay_minutes": 6, "air.co2_warn": 900}, store)
    check("сохранённое читается", saved["ambient.away_delay_minutes"], 6)

    # Браузер не должен уметь переписать номера ножек и скорости шин.
    settings_mod.save({"display.dc": 99, "radar.baud": 1}, store)
    reread = settings_mod.load(store)
    check("посторонние ключи отброшены",
          "display.dc" in reread or "radar.baud" in reread, False)

    settings_mod.save({"ambient.away_delay_minutes": "не число"}, store)
    check("значение неверного типа не затирает прежнее",
          settings_mod.load(store)["ambient.away_delay_minutes"], 6)

    settings_mod.save({"screens.enabled": ["clock.ClockScreen"]}, store)
    base = {"screens": {"enabled": ["a", "b", "c"], "manual": "manual"},
            "ambient": {"enabled": ["x"], "away_delay_minutes": 3},
            "display": {"dc": 25}}
    merged = settings_mod.apply(base, settings_mod.load(store))
    check("список экранов заменён", merged["screens"]["enabled"], ["clock.ClockScreen"])
    check("пауза покоя переопределена", merged["ambient"]["away_delay_minutes"], 6)
    check("не тронутое осталось", merged["screens"]["manual"], "manual")
    check("пины целы", merged["display"]["dc"], 25)
    check("исходный конфиг не испорчен", base["screens"]["enabled"], ["a", "b", "c"])

print("\nСнимок для дашборда")
snap_state = State(now=datetime(2026, 8, 19, 12, 0))
snap_state.desk.presence = True
snap_state.env.co2 = 820
snap_state.health.throttled = True


class RadarStub:
    name, ok, last_error, failures = "presence", True, None, 0

    class last_report:
        state_name, present, distance_cm = "статично", True, 140
        moving_energy, static_energy = 12, 55
        moving_gates = [1, 2, 3, 4, 5, 4, 3, 2, 1]
        static_gates = [5, 6, 7, 8, 9, 8, 7, 6, 5]


snap = status_mod.snapshot(snap_state, [RadarStub()])
check("присутствие в снимке", snap["presence"], True)
check("энергия по воротам доехала", len(snap["radar"]["moving_gates"]), 9)
check("отказ питания попал в снимок", [p["label"] for p in snap["problems"]], ["питание"])

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "status.json"
    # Свежесть считается от настоящего «сейчас», поэтому и метка времени
    # должна быть настоящей: со снимком из прошлого проверялась бы не
    # запись с чтением, а работа календаря.
    fresh_state = State(now=datetime.now())
    fresh_state.env.co2 = 820
    status_mod.write(status_mod.snapshot(fresh_state), path)
    loaded = status_mod.read(path)
    check("снимок читается обратно", loaded["env"]["co2"], 820)
    check("свежий снимок не считается устаревшим", loaded["stale"], False)
    check("временных файлов не осталось", len(list(Path(tmp).iterdir())), 1)

    stale = dict(snap, ts=datetime(2020, 1, 1).isoformat(timespec="seconds"))
    status_mod.write(stale, path)
    check("старый снимок помечен устаревшим", status_mod.read(path)["stale"], True)

check("нет файла — нет снимка", status_mod.read(Path("нет-такого.json")), None)

print("\nПоиск необычных сессий")
from app import anomaly as anomaly_mod  # noqa: E402


def make_window(hour: int = 12, minutes: int = 80, keys: float = 40.0,
                clicks: float = 9.0, sitting: int = 55, breaks: int = 1):
    return anomaly_mod.Window(
        start=datetime(2026, 8, 19, hour), at_desk_minutes=minutes,
        keys_per_minute=keys, clicks_per_minute=clicks,
        longest_sitting=sitting, breaks=breaks, hour=hour, weekend=0)


# Час кодируется точкой на окружности: иначе 23 и 0 оказались бы максимально
# далеки, хотя это соседние часы.
late, early = make_window(hour=23).vector(), make_window(hour=0).vector()
noon = make_window(hour=12).vector()
gap_neighbour = sum((a - b) ** 2 for a, b in zip(late[5:7], early[5:7])) ** 0.5
gap_across = sum((a - b) ** 2 for a, b in zip(late[5:7], noon[5:7])) ** 0.5
check("23 и 0 часов рядом, а не на разных концах", gap_neighbour < gap_across, True)

typical = [make_window(hour=10 + i % 8) for i in range(80)]
check("объяснение без эталона", anomaly_mod.explain(make_window(), []), "нет с чем сравнивать")
check("обычное окно объясняется нейтрально",
      anomaly_mod.explain(make_window(), typical), "непохоже на обычную сессию")
check("марафон назван",
      "без перерыва" in anomaly_mod.explain(make_window(sitting=120), typical), True)
check("всплеск мыши назван",
      "мыши" in anomaly_mod.explain(make_window(clicks=40), typical), True)
check("ночь названа", "ночное время" in anomaly_mod.explain(make_window(hour=3), typical), True)
check("причин не больше двух",
      len(anomaly_mod.explain(make_window(hour=3, sitting=120, clicks=40), typical).split(", ")), 2)

# scikit-learn ставится отдельным флагом и на плате его обычно нет: он
# тянет scipy и почти двести мегабайт, а нужен одному Режиму 6. Пропускаем
# эту часть вместо падения — иначе весь набор тестов нельзя прогнать там,
# где он нужнее всего, то есть на самом устройстве.
try:
    import sklearn  # noqa: F401
    HAS_ML = True
except ImportError:
    HAS_ML = False

if HAS_ML:
    model = anomaly_mod.AnomalyModel()
    check("мало окон — не обучаемся", model.fit(typical[:10]), False)
    check("необученная модель молчит", model.score(make_window())[0], False)
    check("обучение на достаточной выборке", model.fit(typical), True)
    check("типичное окно не помечено", model.score(make_window())[0], False)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.pkl"
        model.save(path)
        restored = anomaly_mod.AnomalyModel()
        check("модель читается обратно", restored.load(path), True)
        check("эталон сохранён", len(restored.reference), len(typical))
        check("битый файл не роняет",
              anomaly_mod.AnomalyModel().load(Path(tmp) / "нет.pkl"), False)
else:
    # Но проверить, что без библиотеки всё молчит, а не падает, обязаны:
    # именно так блок и работает на плате прямо сейчас.
    model = anomaly_mod.AnomalyModel()
    check("без sklearn обучение не падает, а отказывает", model.fit(typical), False)
    check("без sklearn модель молчит", model.score(make_window())[0], False)
    print("    --  scikit-learn не установлен: обучение Режима 6 пропущено")

print(f"\n  провалов: {failed}")
raise SystemExit(1 if failed else 0)
