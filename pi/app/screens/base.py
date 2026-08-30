"""Базовый экран."""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image, ImageDraw

from ..inputs.events import Action
from ..state import State

Box = tuple[float, float, float, float]


class Screen(ABC):
    name: str = ""   # ключ в config.toml
    title: str = ""  # человекочитаемое: превью, логи

    #: Экран просит увести себя обратно на первый режим. Нужно Режиму 4:
    #: подтвердил статус — и вышел, а не остался залипшим в меню.
    exit_requested: bool = False

    #: Экран просит открыть свои подробности по имени. Нужно меню настроек:
    #: выбор пункта поворотом, а открытие — по нажатию.
    requested_detail: str | None = None

    #: Экраны подробностей этого режима. Открываются тапом по карточке
    #: или нажатием с энкодера, листаются поворотом.
    details: list[Screen] = []

    @abstractmethod
    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        """Нарисовать себя на кадре."""

    def hit_zones(self, width: int, height: int) -> dict[str, Box]:
        """Области, реагирующие на тап: имя экрана подробностей -> прямоугольник.

        Раскладку знает только сам экран, поэтому решение «попал ли палец в
        карточку» принимается здесь, а не в распознавателе жестов.
        """
        return {}

    def handle(self, action: Action, state: State) -> bool:
        """Перехватить действие. True — экран обработал сам, листать не надо.

        Нужно Режиму 4: там короткое нажатие подтверждает выбор, а не
        листает дальше (п.10 плана).
        """
        return False


class DetailScreen(Screen):
    """Подробности поверх режима. В карусель не входит."""
