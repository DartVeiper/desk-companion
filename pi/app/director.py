"""Кто решает, что сейчас на экране, и куда девать ввод.

Слои, сверху вниз:

  1. оверлеи — подробности и настройки, стопкой (настройки → яркость)
  2. покой — радар не видит присутствия дольше паузы
  3. карусель семи режимов

Покой — состояние, а не восьмой экран: энкодером в него не долистать и из
него случайно не выпасть. Присутствие вернулось — мгновенно назад на тот
режим, где вы были. Настройки тоже вне карусели: в них попадают только
удержанием на три секунды.
"""

from __future__ import annotations

import random
from datetime import timedelta

from .inputs.events import Action, InputEvent
from .screens.base import Screen
from .screens.registry import ScreenRegistry, instantiate
from .state import State


class Director:
    def __init__(
        self,
        registry: ScreenRegistry,
        ambient: list[Screen],
        settings: Screen | None = None,
        manual: Screen | None = None,
        away_delay: timedelta = timedelta(minutes=3),
        night_style: str | None = None,
        night_from: int = 23,
        night_to: int = 7,
        overlay_timeout: timedelta = timedelta(minutes=2),
    ) -> None:
        self.registry = registry
        self.ambient = ambient
        self.settings = settings
        # Накладка, а не режим карусели. В карусели он был ловушкой:
        # экран забирает себе вращение под выбор статуса, и, докрутив до
        # него, выйти обратно вращением уже нельзя.
        self.manual = manual
        self.away_delay = away_delay
        self.night_style = night_style
        self.night_from = night_from
        self.night_to = night_to
        #: Через сколько без единого нажатия накладка закрывается сама.
        #: Страховка, а не удобство: любая накладка, из которой почему-либо
        #: не выйти, перестаёт быть ловушкой через две минуты.
        self.overlay_timeout = overlay_timeout

        self._ambient_screen: Screen | None = None
        self._ambient_prev: str | None = None
        self._stack: list[Screen] = []
        #: Когда человека видели в последний раз. Публично: это же нужно
        #: строке состояния и тестовой обвязке превью.
        self.last_seen = None
        #: Когда последний раз что-то нажимали. Отдельно от last_seen, и это
        #: существенно: last_seen обновляет ещё и радар, пока человек за
        #: столом, — а именно сидящий за столом человек и застревал в
        #: накладке. Таймаут должен считать нажатия, а не присутствие.
        self.last_input = None
        #: Сколько миллисекунд держат кнопку прямо сейчас. Заполняет драйвер
        #: ввода, читает отрисовка, чтобы нарисовать полосу прогресса.
        self.held_ms: float | None = None

    # ------------------------------------------------------------------ вид

    @property
    def in_ambient(self) -> bool:
        return self._ambient_screen is not None

    @property
    def in_overlay(self) -> bool:
        return bool(self._stack)

    @property
    def overlay_names(self) -> list[str]:
        return [s.name for s in self._stack]

    def current(self, state: State) -> Screen:
        """Что рисовать прямо сейчас. Вызывать каждый кадр."""
        # Время может уехать назад: у Pi нет RTC (батарейный модуль DS3231 —
        # только в бэклоге п.12), и после загрузки часы прыгают, когда
        # приходит первая синхронизация по NTP. Без этой проверки last_seen
        # оказался бы в будущем, и покой не включился бы уже никогда.
        if self.last_seen is None or state.now < self.last_seen:
            self.last_seen = state.now
        if self.last_input is None or state.now < self.last_input:
            self.last_input = state.now

        # Накладка, в которой давно ничего не нажимали, закрывается сама.
        # До проверки покоя, а не после: покой включается только при пустом
        # стеке, и без этой строчки блок, оставшийся в подробностях, не
        # уходил в покой никогда — даже когда в комнате никого нет.
        if self._stack and state.now - self.last_input >= self.overlay_timeout:
            self.close_overlays()

        if state.desk.presence:
            self.last_seen = state.now
            self._ambient_screen = None
        elif not self._stack and self._ambient_screen is None:
            # Гистерезис обязателен: радар периодически теряет неподвижного
            # человека, и без паузы экран дёргался бы туда-сюда.
            if state.now - self.last_seen >= self.away_delay:
                self._ambient_screen = self._pick_ambient(state)

        if self._stack:
            return self._stack[-1]
        if self._ambient_screen is not None:
            return self._ambient_screen
        return self.registry.current

    def _is_night(self, state: State) -> bool:
        hour = state.now.hour
        if self.night_from <= self.night_to:
            return self.night_from <= hour < self.night_to
        return hour >= self.night_from or hour < self.night_to

    def _pick_ambient(self, state: State) -> Screen | None:
        if not self.ambient:
            return None
        if self.night_style and self._is_night(state):
            for screen in self.ambient:
                if screen.name == self.night_style:
                    return screen

        # Днём ночной стиль из ротации убираем: красный циферблат в два часа
        # дня выглядит поломкой, а не задумкой.
        pool = [s for s in self.ambient if s.name != self.night_style] or self.ambient
        # Стиль новый при каждом входе, но не тот же, что был в прошлый раз.
        fresh = [s for s in pool if s.name != self._ambient_prev] or pool
        chosen = random.choice(fresh)
        self._ambient_prev = chosen.name
        return chosen

    # ----------------------------------------------------------------- ввод

    def handle(self, event: InputEvent, state: State) -> None:
        # Любой ввод означает, что человек здесь, даже если радар не согласен.
        self.last_seen = state.now
        self.last_input = state.now
        self.held_ms = None

        if self._ambient_screen is not None:
            self._ambient_screen = None
            return  # первое касание только будит, ничего не переключая

        action = event.action
        if action is Action.SETTINGS:
            self.open_settings()
            return

        if action is Action.TAP:
            action = self._resolve_tap(event)
            if action is None:
                return

        if self._stack:
            self._handle_overlay(action, state)
            return

        screen = self.registry.current
        if screen.handle(action, state):
            self._apply_requests(screen)
            return

        if action is Action.NEXT:
            self.registry.next()
        elif action is Action.PREV:
            self.registry.prev()
        elif action is Action.HOLD and self.manual is not None:
            self._stack.append(self.manual)

    def open_settings(self) -> None:
        if self.settings is None:
            return
        self._stack = [self.settings]

    def close_overlays(self) -> None:
        """Закрыть всё поверх карусели: по таймауту, при уходе в покой,
        при прыжке на конкретный режим."""
        self._stack.clear()

    # Совместимость со старым именем.
    close_detail = close_overlays

    def _apply_requests(self, screen: Screen) -> None:
        """Отработать просьбы экрана, выставленные внутри handle()."""
        if screen.requested_detail:
            target = next((d for d in screen.details if d.name == screen.requested_detail), None)
            screen.requested_detail = None
            if target is not None:
                self._stack.append(target)
                return
        if screen.exit_requested:
            screen.exit_requested = False
            if self._stack:
                self._stack.pop()
            else:
                self.registry.home()

    def _handle_overlay(self, action: Action, state: State) -> None:
        top = self._stack[-1]
        if top.handle(action, state):
            self._apply_requests(top)
            return

        if action in (Action.NEXT, Action.PREV):
            # Переключать соседние накладки вращением — право экрана, а не
            # умолчание: раньше это было умолчанием, и, покрутив в
            # диагностике, человек оказывался в яркости, куда не собирался.
            if getattr(top, "cycle_siblings", False):
                siblings = self._parent_of_top().details
                if len(siblings) > 1 and top in siblings:
                    i = siblings.index(top)
                    step = 1 if action is Action.NEXT else -1
                    self._stack[-1] = siblings[(i + step) % len(siblings)]
                return
            # А вот молчать нельзя. Здесь стоял пустой возврат, и это была
            # ловушка: на экране подробностей ни свайп, ни вращение не
            # делали ровно ничего. Тап в левую или правую треть тоже —
            # он превращается в PREV/NEXT и приходил сюда же. Итого две
            # трети экрана и вся крутилка были мертвы, и выглядело это
            # намертво зависшим блоком.
            self._stack.pop()
            return

        # Всё остальное поднимает на слой выше. Залипнуть нельзя.
        self._stack.pop()

    def _parent_of_top(self) -> Screen:
        return self._stack[-2] if len(self._stack) > 1 else self.registry.current

    def _resolve_tap(self, event: InputEvent) -> Action | None:
        """Тап: сперва спрашиваем экран, потом падаем на навигацию по третям."""
        from . import theme

        screen = self._stack[-1] if self._stack else self.registry.current
        x, y = event.x or 0, event.y or 0

        home = screen.home_zone(theme.WIDTH, theme.HEIGHT)
        if home is not None and home[0] <= x <= home[2] and home[1] <= y <= home[3]:
            # Не «на шаг назад», а сразу на первый экран. Из глубины в два
            # слоя выбираться по одному нажатию — это и есть та
            # потерянность, ради которой кнопку и добавили.
            self.close_overlays()
            self.registry.home()
            return None

        for detail_name, (x0, y0, x1, y1) in screen.hit_zones(theme.WIDTH, theme.HEIGHT).items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                target = next((d for d in screen.details if d.name == detail_name), None)
                if target is not None:
                    self._stack.append(target)
                    return None

        third = theme.WIDTH / 3
        if x < third:
            return Action.PREV
        if x > third * 2:
            return Action.NEXT
        return Action.SELECT


def load(config_path, registry: ScreenRegistry) -> Director:
    from .screens.registry import load_config

    return from_config(load_config(config_path), registry)


def from_config(config: dict, registry: ScreenRegistry) -> Director:
    """Собрать из уже прочитанного конфига.

    Нужно, когда поверх него наложены настройки из браузера: читать файл
    второй раз означало бы потерять их.
    """
    cfg = config.get("ambient", {})
    screens = config.get("screens", {})
    settings_entry = screens.get("settings")
    manual_entry = screens.get("manual")
    return Director(
        registry,
        [instantiate(e) for e in cfg.get("enabled", [])],
        settings=instantiate(settings_entry) if settings_entry else None,
        manual=instantiate(manual_entry) if manual_entry else None,
        away_delay=timedelta(minutes=cfg.get("away_delay_minutes", 3)),
        night_style=cfg.get("night_style"),
        night_from=cfg.get("night_from", 23),
        night_to=cfg.get("night_to", 7),
        overlay_timeout=timedelta(
            seconds=screens.get("overlay_timeout_seconds", 120)),
    )
