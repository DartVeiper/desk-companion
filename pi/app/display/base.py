"""Общий интерфейс дисплея.

Два бэкенда с одним API: PreviewDisplay отдаёт PNG (разработка на Windows),
St7796sDisplay шлёт кадр по SPI (Pi). Экраны о разнице не знают, поэтому
интерфейс можно рисовать и вылизывать до того, как приедет железо.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image

from .. import theme


class Display(ABC):
    width: int = theme.WIDTH
    height: int = theme.HEIGHT

    @abstractmethod
    def show(self, image: Image.Image) -> None:
        """Вывести готовый кадр."""

    def new_frame(self) -> Image.Image:
        return Image.new("RGB", (self.width, self.height), theme.BG)

    def close(self) -> None:
        pass
