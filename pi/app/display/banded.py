"""Частичная перерисовка полосами — главный рычаг производительности.

Кадр 480x320 в RGB565 — это 307 200 байт. На SPI 40 МГц он уходит ~61 мс,
а провода-перемычки 10 см реально держат 20-32 МГц, то есть ближе к 100 мс.
Гнать столько на каждую смену минуты бессмысленно: меняются две-три полосы
из шестнадцати.

Делим кадр на горизонтальные полосы, numpy сравнивает свежий с предыдущим,
по шине уходят только изменившиеся. Соседние изменившиеся полосы склеиваем
в один кусок: у каждой передачи есть постоянные накладные расходы на
установку окна, и три отдельных куска дороже одного тройного.
"""

from __future__ import annotations

from abc import abstractmethod

import numpy as np
from PIL import Image

from .base import Display

BANDS = 16
# Сетка плиток: 60x20 точек при 480x320. Мельче дробить нет смысла —
# накладные расходы на установку окна начнут съедать выигрыш.
COLS, ROWS = 8, 16


def to_rgb565(image: Image.Image) -> np.ndarray:
    """RGB888 -> RGB565, массив uint16 формы (высота, ширина).

    Именно тут Python был бы безнадёжен: попиксельный перегон 153 600 точек
    на Pi Zero занял бы секунды. numpy делает это одной векторной операцией.
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    red = arr[:, :, 0].astype(np.uint16) >> 3
    green = arr[:, :, 1].astype(np.uint16) >> 2
    blue = arr[:, :, 2].astype(np.uint16) >> 3
    return (red << 11) | (green << 5) | blue


def band_bounds(height: int, bands: int = BANDS) -> list[tuple[int, int]]:
    """Границы полос. Последняя добирает остаток, если высота не делится."""
    step = -(-height // bands)  # округление вверх
    out = []
    for start in range(0, height, step):
        out.append((start, min(height, start + step)))
    return out


def changed_runs(
    current: np.ndarray,
    previous: np.ndarray | None,
    bands: int = BANDS,
) -> list[tuple[int, int]]:
    """Изменившиеся горизонтальные полосы. Пусто — кадр не изменился."""
    if previous is None or previous.shape != current.shape:
        return [(0, current.shape[0])]

    runs: list[tuple[int, int]] = []
    for start, end in band_bounds(current.shape[0], bands):
        if np.array_equal(current[start:end], previous[start:end]):
            continue
        if runs and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], end)  # склеиваем с предыдущей
        else:
            runs.append((start, end))
    return runs


def changed_tiles(
    current: np.ndarray,
    previous: np.ndarray | None,
    cols: int = COLS,
    rows: int = ROWS,
) -> list[tuple[int, int, int, int]]:
    """Изменившиеся прямоугольники (x0, y0, x1, y1), правый и нижний край
    не включаются.

    Делить кадр только на горизонтальные полосы мало: цифры часов высотой
    132 px занимают шесть полос из шестнадцати, и смена минуты гонит больше
    трети кадра, хотя реально меняется узкая колонка справа. Сетка режет
    и по ширине, поэтому уходит именно изменившийся кусок.

    Соседние по горизонтали плитки склеиваются в один прямоугольник, а
    одинаковые прямоугольники в соседних рядах — в один вертикально:
    у каждой передачи есть постоянная плата за установку окна.
    """
    height, width = current.shape
    if previous is None or previous.shape != current.shape:
        return [(0, 0, width, height)]

    out: list[tuple[int, int, int, int]] = []
    col_bounds = band_bounds(width, cols)
    for top, bottom in band_bounds(height, rows):
        spans: list[tuple[int, int]] = []
        for left, right in col_bounds:
            if np.array_equal(current[top:bottom, left:right],
                              previous[top:bottom, left:right]):
                continue
            if spans and spans[-1][1] == left:
                spans[-1] = (spans[-1][0], right)
            else:
                spans.append((left, right))

        for left, right in spans:
            if out and out[-1][3] == top and out[-1][0] == left and out[-1][2] == right:
                out[-1] = (left, out[-1][1], right, bottom)  # тот же столбец ниже
            else:
                out.append((left, top, right, bottom))
    return out


class BandedDisplay(Display):
    """Дисплей, который шлёт только изменившиеся куски кадра.

    Наследник реализует write_window — собственно передачу по шине.
    """

    cols, rows = COLS, ROWS

    def __init__(self) -> None:
        self._previous: np.ndarray | None = None
        self.bytes_sent = 0
        self.windows_sent = 0
        self.full_frames = 0
        self.partial_frames = 0

    def show(self, image: Image.Image) -> None:
        frame = to_rgb565(image)
        tiles = changed_tiles(frame, self._previous, self.cols, self.rows)
        if not tiles:
            return

        for x0, y0, x1, y1 in tiles:
            # Порядок байтов на шине — старший первым, как ждёт контроллер.
            payload = frame[y0:y1, x0:x1].astype(">u2").tobytes()
            self.write_window(x0, y0, x1 - 1, y1 - 1, payload)
            self.bytes_sent += len(payload)
            self.windows_sent += 1

        if tiles == [(0, 0, frame.shape[1], frame.shape[0])]:
            self.full_frames += 1
        else:
            self.partial_frames += 1
        self._previous = frame

    def invalidate(self) -> None:
        """Забыть предыдущий кадр: следующий уйдёт целиком.

        Нужно после того, как в экран писал кто-то ещё — например скрипт
        калибровки тача.
        """
        self._previous = None

    @abstractmethod
    def write_window(self, x0: int, y0: int, x1: int, y1: int, payload: bytes) -> None:
        """Передать кусок кадра в прямоугольник экрана."""
