"""Режим 4: Manual Override (п.10 плана).

Вход — долгое нажатие с энкодера или с тача. Внутри поворот выбирает
статус, короткое нажатие подтверждает и выходит обратно. Без выхода после
подтверждения легко залипнуть в меню, поэтому SELECT ставит exit_requested.

Автосброс через 4-6 часов обязателен (п.6 плана): без таймера возврата
легко забыть, что «не беспокоить» стоит с прошлой недели.
"""

from __future__ import annotations

from datetime import timedelta

from PIL import Image, ImageDraw

from .. import theme
from ..inputs.events import Action
from ..state import State
from . import widgets as w
from .base import Screen

AUTO_RESET = timedelta(hours=5)
OPTIONS: tuple[tuple[str | None, str], ...] = (
    (None, "Авто"),
    ("занят", "Занят"),
    ("не беспокоить", "Не беспокоить"),
    ("на созвоне", "На созвоне"),
    ("отошёл", "Отошёл"),
    ("сплю", "Сплю"),
)


class ManualScreen(Screen):
    name = "manual"
    title = "Ручной статус"

    def __init__(self) -> None:
        self._index = 0

    # Кнопки «домой» здесь намеренно нет. В правом углу живёт обратный
    # отсчёт до автосброса, а он тут важнее: именно он объясняет, почему
    # статус однажды пропадёт сам. Выход с этого экрана и так очевиден —
    # выбор статуса закрывает его, а любое другое действие уводит наверх.
    def handle(self, action: Action, state: State) -> bool:
        if action is Action.NEXT:
            self._index = (self._index + 1) % len(OPTIONS)
            return True
        if action is Action.PREV:
            self._index = (self._index - 1) % len(OPTIONS)
            return True
        if action is Action.SELECT:
            value = OPTIONS[self._index][0]
            state.desk.manual_status = value
            state.desk.manual_until = None if value is None else state.now + AUTO_RESET
            self.exit_requested = True
            return True
        return False

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        active = state.desk.manual_status

        left = ""
        if state.desk.manual_until:
            left = "сброс через " + w.duration(
                (state.desk.manual_until - state.now).total_seconds())
        w.header(draw, width, self.title, left or None,
                 theme.WARN if active else theme.DIM, state=state)

        cols, rows = 3, 2
        cell_w = (width - w.PAD * 2 - w.GAP * (cols - 1)) / cols
        cell_h = (height - 66 - w.PAD - w.GAP) / rows
        for i, (value, label) in enumerate(OPTIONS):
            x = w.PAD + (i % cols) * (cell_w + w.GAP)
            y = 66 + (i // cols) * (cell_h + w.GAP)
            box = (x, y, x + cell_w, y + cell_h)

            chosen = value == active
            highlighted = i == self._index
            w.card(draw, box, (48, 42, 26) if chosen else theme.SURFACE)
            if highlighted:
                draw.rounded_rectangle(box, radius=14, outline=theme.ACCENT, width=3)

            draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 - 6), label,
                      font=w.fit_font(draw, label, cell_w - 20, theme.BODY),
                      fill=theme.WARN if chosen else theme.FG, anchor="mm")
            if chosen:
                draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 + 20), "активен",
                          font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")
