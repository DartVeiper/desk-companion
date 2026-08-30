"""Бэкенд разработки: кадр уходит в PNG, а не по SPI.

Ключевой момент — превью гоняет тот же самый код отрисовки, что и Pi.
Если бы страница превью рисовала интерфейс своими средствами, получились бы
две реализации одного макета, и они разъехались бы на второй неделе.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .base import Display


class PreviewDisplay(Display):
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else None
        self.frames_shown = 0
        self.last: Image.Image | None = None

    def show(self, image: Image.Image) -> None:
        self.last = image.copy()
        self.frames_shown += 1
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            image.save(self._path)

    def png_bytes(self) -> bytes:
        """Последний кадр для отдачи в браузер."""
        if self.last is None:
            raise RuntimeError("кадр ещё не отрисован")
        buf = io.BytesIO()
        self.last.save(buf, format="PNG")
        return buf.getvalue()
