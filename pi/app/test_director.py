"""Тесты маршрутизации экранов. Железо не нужно: py app/test_director.py"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.director import Director
from app.inputs.events import Action, InputEvent
from app.screens.ambient import BigDigitsAmbient, MatrixAmbient, NightRedAmbient
from app.screens.clock import ClockScreen
from app.screens.manual import ManualScreen
from app.screens.registry import ScreenRegistry
from app.screens.settings import SettingsScreen
from app.screens.streak import StreakScreen
from app.sources import ManualResetSource
from app.state import Health, State

AWAY = timedelta(minutes=3)
failed = 0


def check(name: str, got, expected) -> None:
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name:44} {got}")


def make(hour: int = 14) -> tuple[Director, State]:
    # Ручного статуса в карусели нет намеренно: он накладка, как настройки.
    registry = ScreenRegistry([ClockScreen(), StreakScreen()])
    director = Director(
        registry,
        [BigDigitsAmbient(), MatrixAmbient(), NightRedAmbient()],
        settings=SettingsScreen(),
        manual=ManualScreen(),
        away_delay=AWAY, night_style="ambient_night", night_from=23, night_to=7,
    )
    state = State(now=datetime(2026, 8, 19, hour, 0))
    state.desk.presence = True
    director.current(state)
    return director, state


def tap(director: Director, state: State, x: int, y: int) -> None:
    director.handle(InputEvent(Action.TAP, "touch", 0.0, x, y), state)


def press(director: Director, state: State, action: Action) -> None:
    director.handle(InputEvent(action, "encoder", 0.0), state)


def absent(state: State, minutes: int) -> None:
    state.desk.presence = False
    state.now = state.now + timedelta(minutes=minutes)


print("Подробности")
d, s = make()
check("старт — карусель", d.current(s).name, "clock")
tap(d, s, 95, 250)
check("тап по карточке погоды", d.current(s).name, "weather_detail")
press(d, s, Action.NEXT)
check("поворот в подробностях никуда не уводит", d.current(s).name, "weather_detail")
press(d, s, Action.SELECT)
check("нажатие закрывает", d.current(s).name, "clock")
tap(d, s, 400, 250)
check("тап по карточке комнаты", d.current(s).name, "air_detail")
tap(d, s, 240, 160)
check("тап закрывает", d.current(s).name, "clock")
press(d, s, Action.SELECT)
check("нажатие на часах ничего не открывает", d.current(s).name, "clock")

print("\nТап мимо карточек — навигация по третям")
d, s = make()
tap(d, s, 440, 60)
check("правая треть — вперёд", d.current(s).name, "streak")
tap(d, s, 40, 60)
check("левая треть — назад", d.current(s).name, "clock")

print("\nПокой по присутствию")
d, s = make()
absent(s, 1)
check("нет 1 мин — пауза не вышла", d.current(s).name, "clock")
absent(s, 1)
check("нет 2 мин — пауза не вышла", d.current(s).name, "clock")
absent(s, 2)
check("нет 4 мин — ушли в покой", d.current(s).name.startswith("ambient_"), True)
press(d, s, Action.NEXT)
check("ввод будит", d.current(s).name, "clock")
check("но экран не пролистался", d.registry.current.name, "clock")
s.desk.presence = True
check("человек вернулся", d.current(s).name, "clock")

print("\nПрыжок часов назад — NTP после загрузки без RTC")
d, s = make()
s.now = s.now - timedelta(hours=5)
absent(s, 1)
check("сразу после отката отсчёт начат заново", d.current(s).name, "clock")
absent(s, 5)
check("дальше покой включается как обычно", d.current(s).name.startswith("ambient_"), True)

# Ночное время задаём сразу: перевод часов назад по ходу теста упирается
# в защиту от прыжка NTP и справедливо сбрасывает отсчёт.
print("\nВыбор стиля")
d, s = make(hour=3)
for i in range(4):
    absent(s, 5)
    name = d.current(s).name
    s.desk.presence = True
    d.current(s)
    check(f"ночью вход #{i + 1}", name, "ambient_night")

d, s = make()
seen = set()
for _ in range(12):
    absent(s, 5)
    seen.add(d.current(s).name)
    s.desk.presence = True
    d.current(s)
check("днём ночной стиль не выпадает", "ambient_night" in seen, False)
check("днём чередуются оба дневных", seen, {"ambient_digits", "ambient_matrix"})

print("\nНастройки: вход только удержанием 3 с")
d, s = make()
for _ in range(len(d.registry.screens) + 1):
    press(d, s, Action.NEXT)
check("каруселью до настроек не долистать", "settings" in d.overlay_names, False)
press(d, s, Action.SETTINGS)
check("удержание 3 с открывает настройки", d.current(s).name, "settings")
press(d, s, Action.SELECT)
check("нажатие открывает выбранный пункт", d.current(s).name, "brightness")
before = s.brightness
press(d, s, Action.NEXT)
check("поворот меняет яркость, а не листает", s.brightness, before + 5)
check("экран остался на яркости", d.current(s).name, "brightness")
press(d, s, Action.SELECT)
check("нажатие возвращает в меню", d.current(s).name, "settings")
press(d, s, Action.NEXT)
press(d, s, Action.SELECT)
check("второй пункт — диагностика", d.current(s).name, "diagnostics")
press(d, s, Action.SELECT)
press(d, s, Action.PREV)
press(d, s, Action.PREV)
press(d, s, Action.SELECT)
# Проверяем сам факт выхода, а не конкретный режим: карусель выше по тесту
# легитимно уехала со стартового экрана.
check("пункт «Выйти» закрывает настройки", d.in_overlay, False)
press(d, s, Action.SETTINGS)
d.close_overlays()
check("настройки закрываются снаружи", d.in_overlay, False)

print("\nКнопка «домой» на накладках")
from app.screens import widgets as _w
_hx = (_w.home_box(480)[0] + _w.home_box(480)[2]) / 2
_hy = (_w.home_box(480)[1] + _w.home_box(480)[3]) / 2

d, s = make()
press(d, s, Action.NEXT)          # ушли с первого экрана на второй
press(d, s, Action.SETTINGS)      # и открыли поверх накладку
check("зашли вглубь", d.in_overlay, True)
tap(d, s, int(_hx), int(_hy))
check("тап по стрелке закрыл накладку", d.in_overlay, False)
check("и вернул на первый экран, а не на второй", d.current(s).name, "clock")

# Из двух слоёв вглубь — тоже одним нажатием, ради этого кнопка и нужна.
d, s = make()
press(d, s, Action.SETTINGS)
press(d, s, Action.SELECT)
check("два слоя накладок", len(d.overlay_names), 2)
tap(d, s, int(_hx), int(_hy))
check("одна стрелка выводит из глубины", d.in_overlay, False)

# А на экранах карусели кнопки нет: выходить оттуда некуда.
d, s = make()
check("у режима карусели зоны нет",
      d.current(s).home_zone(480, 320), None)

print("\nРучной статус недостижим вращением — иначе из него не выйти")
d, s = make()
seen = set()
for _ in range(12):
    press(d, s, Action.NEXT)
    seen.add(d.current(s).name)
check("карусель ручной статус не содержит", "manual" in seen, False)
check("в карусели только включённые режимы", sorted(seen), ["clock", "streak"])
d, s = make()
press(d, s, Action.HOLD)
press(d, s, Action.PREV)
check("вращение в накладке её не закрывает", d.in_overlay, True)

print("\nАвтосброс ручного статуса — шаг 8 плана")
d, s = make()
reset = ManualResetSource()
press(d, s, Action.HOLD)
check("удержание 0,7 с открывает ручной статус", d.current(s).name, "manual")
check("и это накладка, а не режим карусели", d.in_overlay, True)
press(d, s, Action.NEXT)
check("вращение внутри перебирает статусы", d.current(s).name, "manual")
press(d, s, Action.SELECT)
check("выбор статуса закрывает накладку", d.in_overlay, False)
check("статус выставлен", s.desk.manual_status is not None, True)
check("срок сброса назначен", s.desk.manual_until is not None, True)
s.now = s.now + timedelta(hours=4)
reset.update(s)
check("через 4 ч статус ещё держится", s.desk.manual_status is not None, True)
s.now = s.now + timedelta(hours=2)
reset.update(s)
check("через 6 ч сброшен сам", s.desk.manual_status, None)
check("срок тоже снят", s.desk.manual_until, None)

print("\nСтрока состояния молчит, пока всё в порядке")
d, s = make()
check("исправный блок — отказов нет", s.problems(), [])
s.pc.last_heartbeat = None
check("выключенный ПК отказом не считается", s.problems(), [])
s.health = Health(throttled=True, wifi_ok=False, scd41_ok=False)
labels = [p[0] for p in s.problems()]
check("отказы найдены", labels, ["питание", "нет сети", "нет CO2"])
check("критичное первым", s.problems()[0][2], True)
check("датчик — не критично", s.problems()[2][2], False)

print(f"\n  провалов: {failed}")
raise SystemExit(1 if failed else 0)
