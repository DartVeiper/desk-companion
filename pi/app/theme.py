"""Оформление отдельно от логики экранов.

Всё, что хочется подвинуть глазами — размеры, отступы, цвета — живёт здесь.
Правка внешнего вида не требует лезть в код экрана.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

# ST7796S. В п.2 плана был ILI9341 240x320 — железо оказалось другим.
WIDTH = 480
HEIGHT = 320

# Тёмная тема: блок светит на столе круглосуточно, светлый фон ночью слепит.
BG = (8, 10, 14)
SURFACE = (22, 26, 33)
LINE = (44, 50, 60)
FG = (234, 238, 245)
DIM = (118, 128, 145)
ACCENT = (120, 180, 255)
OK = (120, 210, 140)
WARN = (240, 190, 96)
ALERT = (238, 108, 108)

PAD = 18  # поле от края экрана
GAP = 10  # промежуток между блоками

CLOCK = 132
H1 = 46
H2 = 30
BODY = 22
SMALL = 17
TINY = 14

# Шрифт лежит в репозитории и берётся только отсюда. Системные шрифты не
# трогаем сознательно: на Windows подхватывался бы Segoe UI, на Pi — DejaVu,
# метрики у них разные, и превью врало бы про то, влезает ли текст.
FONT_DIR = Path(__file__).parent / "Nunito"
FONT_FAMILY = "Nunito"

# Nunito отдаёт весь диапазон, поэтому вес — параметр, а не только «жирный».
LIGHT, REGULAR, SEMIBOLD, BOLD, EXTRABOLD, BLACK = (
    "Light", "Regular", "SemiBold", "Bold", "ExtraBold", "Black",
)


@lru_cache(maxsize=128)
def font(size: int, bold: bool = False, weight: str | None = None) -> ImageFont.FreeTypeFont:
    name = weight or (BOLD if bold else REGULAR)
    path = FONT_DIR / f"{FONT_FAMILY}-{name}.ttf"
    if not path.exists():
        raise FileNotFoundError(
            f"нет файла шрифта: {path}\n"
            f"положи семейство {FONT_FAMILY} в {FONT_DIR}"
        )
    return ImageFont.truetype(str(path), size)


def co2_color(ppm: int | None) -> tuple[int, int, int]:
    """Пороги под пересмотр: попадут в настройки дашборда (п.7)."""
    if ppm is None:
        return DIM
    if ppm < 800:
        return OK
    if ppm < 1400:
        return WARN
    return ALERT
