"""Калибровка тача из приложения — на работающем блоке.

Раньше калибровка была отдельным скриптом: остановить сервис, запустить
его по ssh, нажать в крестики, запустить сервис обратно. Для человека,
собравшего блок по инструкции, это три незнакомых команды ради того, чтобы
кнопки перестали мазать. Теперь она запускается кнопкой в приложении, а
крестики рисует сам сервис.

Как это устроено. Сервис и дашборд — разные процессы, и общаются они так
же, как с настройками: файлами в папке приложения.

    touch-calibration.request   дашборд пишет «начать» или «отменить»,
                                сервис читает и удаляет
    touch-calibration.json      сервис пишет, на каком шаге калибровка,
                                дашборд читает и отдаёт приложению

Нажатия во время калибровки идут мимо распознавателя жестов: иначе нажатие
в крестик у края экрана листало бы карусель.

Калибруются только координаты. Сила нажатия — дело ползунка
чувствительности, и калибровка его не трогает.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ImageDraw

from . import theme
from .drivers import xpt2046
from .screens import widgets
from .screens.base import Screen

HERE = Path(__file__).resolve().parent
REQUEST = HERE / "touch-calibration.request"
STATE = HERE / "touch-calibration.json"

#: Отступ крестиков от края. Ближе к краю в крестик не попасть пальцем:
#: рамка корпуса и толщина пальца сдвигают нажатие внутрь.
MARGIN = 34

#: Худший допустимый промах по самим нажатиям. Больше — значит, одно из
#: нажатий ушло мимо крестика, и такая калибровка испортила бы тач сильнее,
#: чем было.
MAX_MISS_PX = 22

#: Без нажатий столько времени — калибровка сама отменяется. Иначе блок,
#: от которого человек отошёл, так и остался бы с крестиком на экране.
IDLE_TIMEOUT_S = 180

#: Сколько показывать итог на экране блока. Человек смотрит на блок, а не
#: в приложение, и должен увидеть, что всё получилось.
RESULT_S = 2.5


def targets(width: int = theme.WIDTH, height: int = theme.HEIGHT) -> list[tuple[int, int]]:
    """Четыре угла по часовой стрелке, начиная с левого верхнего."""
    return [(MARGIN, MARGIN), (width - MARGIN, MARGIN),
            (width - MARGIN, height - MARGIN), (MARGIN, height - MARGIN)]


@dataclass
class Session:
    """Одна калибровка: какие крестики нажаты и что прочла панель."""

    id: str
    started: float = field(default_factory=time.monotonic)
    last_press: float = field(default_factory=time.monotonic)
    presses: list[xpt2046.Press] = field(default_factory=list)
    #: Итог для экрана: "done", "failed" или "cancelled". None — идёт.
    result: str | None = None
    result_at: float = 0.0
    detail: str = ""

    @property
    def total(self) -> int:
        return len(targets())

    @property
    def step(self) -> int:
        """Номер крестика, который сейчас на экране, с единицы."""
        return min(len(self.presses) + 1, self.total)

    @property
    def target(self) -> tuple[int, int] | None:
        if self.result is not None or len(self.presses) >= self.total:
            return None
        return targets()[len(self.presses)]

    def add(self, raw: tuple[int, int]) -> bool:
        """Записать нажатие в текущий крестик. True — все четыре собраны."""
        target = self.target
        if target is None:
            return False
        self.presses.append((target, raw))
        self.last_press = time.monotonic()
        return len(self.presses) >= self.total

    def idle(self, now: float) -> bool:
        return self.result is None and now - self.last_press > IDLE_TIMEOUT_S

    def finish(self, result: str, detail: str = "") -> None:
        self.result = result
        self.detail = detail
        self.result_at = time.monotonic()

    def result_shown(self, now: float) -> bool:
        return self.result is not None and now - self.result_at > RESULT_S


# ---------------------------------------------------------------- файлы


def read_request() -> dict | None:
    """Забрать просьбу дашборда. Файл удаляется: просьба исполняется раз."""
    try:
        payload = json.loads(REQUEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        REQUEST.unlink(missing_ok=True)
    return payload if isinstance(payload, dict) else None


def write_request(action: str, request_id: str) -> None:
    _atomic(REQUEST, {"action": action, "id": request_id})


def write_state(session: Session | None, state: str, detail: str = "") -> None:
    _atomic(STATE, {
        "id": session.id if session else "",
        "state": state,
        "step": session.step if session else 0,
        "total": session.total if session else len(targets()),
        "detail": detail,
        "updated": time.time(),
    })


def read_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"id": "", "state": "idle", "step": 0, "total": len(targets()),
            "detail": "", "updated": 0}


def _atomic(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def save(calibration: xpt2046.Calibration) -> None:
    """Записать координаты в calibration.toml, не трогая силу нажатия."""
    from tools import _calibration

    _calibration.save(HERE / "calibration.toml", "touch", calibration.to_dict(), notes={
        "x_min":
            "Границы сырых значений АЦП этой панели, продолженные до краёв\n"
            "экрана. Подобраны калибровкой по четырём углам.",
        "swap_xy":
            "Ориентация определена по самим нажатиям, а не взята из конфига.",
    })


# ---------------------------------------------------------------- экран


class CalibrationScreen(Screen):
    """Крестик, который надо нажать, и где мы сейчас."""

    name = "touch_calibration"
    title = "Калибровка экрана"

    def __init__(self, session: Session) -> None:
        self.session = session

    def render(self, state, draw: ImageDraw.ImageDraw, frame) -> None:
        width, height = theme.WIDTH, theme.HEIGHT
        draw.rectangle((0, 0, width, height), fill=theme.BG)
        session = self.session

        if session.result is not None:
            color, caption = {
                "done": (theme.OK, "готово"),
                "failed": (theme.ALERT, "не вышло"),
            }.get(session.result, (theme.DIM, "отменено"))
            draw.text((width // 2, height // 2 - 24), caption,
                      font=theme.font(40, bold=True), fill=color, anchor="mm")
            if session.detail:
                # Причина отказа бывает длинной — в одну строку она
                # упиралась в край экрана. Переносим по словам.
                font = theme.font(theme.SMALL)
                lines = widgets.wrap(draw, session.detail, width - 60, font)
                for index, line in enumerate(lines):
                    draw.text((width // 2, height // 2 + 22 + index * 24), line,
                              font=font, fill=theme.DIM, anchor="mm")
            return

        target = session.target
        if target is None:
            return
        x, y = target
        # Крестик крупный, а центр отмечен кольцом: целиться легче в точку,
        # чем в пересечение тонких линий.
        draw.line((x - 22, y, x + 22, y), fill=theme.ACCENT, width=3)
        draw.line((x, y - 22, x, y + 22), fill=theme.ACCENT, width=3)
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=theme.ACCENT, width=3)

        # Подпись — в середине, подальше от крестиков, чтобы не заслонять.
        draw.text((width // 2, height // 2 - 16), "нажми точно в крестик",
                  font=theme.font(theme.BODY, bold=True), fill=theme.FG, anchor="mm")
        draw.text((width // 2, height // 2 + 14),
                  f"{session.step} / {session.total}",
                  font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")
        draw.text((width // 2, height // 2 + 42), "кнопка энкодера — отмена",
                  font=theme.font(14), fill=theme.DIM, anchor="mm")
