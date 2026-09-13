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


class CachedFont(ImageFont.FreeTypeFont):
    """Шрифт, который помнит уже растеризованные строки.

    Три четверти времени сборки кадра уходило на растеризацию текста:
    сорок миллисекунд на кадр, из них тридцать — превращение одних и тех
    же цифр в картинку заново. Часы же меняют время раз в минуту, а
    подписи на карточках не меняются вовсе. С кешем кадр собирается за
    семь миллисекунд.

    Наследник, а не обёртка. Обёртка с __getattr__ выглядит аккуратнее и
    работает — ровно до первого несовпадения версий. Pillow 11 на плате
    спрашивает у шрифта getmask2, Pillow 12 на машине разработки —
    getmask; обёртка перехватывала только первый, и на второй Pillow тихо
    уходил в запасной путь, который не умеет привязку текста (anchor).
    Часы при этом рисовались с уехавшей за экран последней цифрой, то
    есть инструменты проверки вёрстки врали бы, не сказав ни слова.
    Наследник же остаётся настоящим FreeTypeFont со всеми его потрохами,
    и подменяет только растеризацию.

    Ключ кеша собирается из всех аргументов вызова без разбора, через
    repr. Разбирать их по именам значит знать сигнатуру конкретной версии
    Pillow — а именно на этом знании обёртка и погорела.
    """

    #: Потолок записей. Нужен не ради памяти (растр строки — килобайты), а
    #: чтобы неограниченно растущий словарь не стал утечкой на устройстве,
    #: которое работает месяцами.
    LIMIT = 256

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._masks: dict = {}
        self.hits = 0
        self.misses = 0

    def _remember(self, kind: str, args: tuple, kwargs: dict, make):
        key = (kind, repr(args), repr(sorted(kwargs.items())))
        found = self._masks.get(key)
        if found is not None:
            self.hits += 1
            return found
        self.misses += 1
        result = make()
        if len(self._masks) >= self.LIMIT:
            # Простая очистка вместо вытеснения по давности: строк у нас
            # десятки, до потолка доходит разве что счётчик секунд, и
            # платить за учёт давности каждым кадром дороже, чем раз в
            # сутки нарисовать всё заново.
            self._masks.clear()
        self._masks[key] = result
        return result

    def getmask(self, *args, **kwargs):
        return self._remember(
            "1", args, kwargs, lambda: super(CachedFont, self).getmask(*args, **kwargs))

    def getmask2(self, *args, **kwargs):
        return self._remember(
            "2", args, kwargs, lambda: super(CachedFont, self).getmask2(*args, **kwargs))


@lru_cache(maxsize=128)
def font(size: int, bold: bool = False, weight: str | None = None) -> ImageFont.FreeTypeFont:
    name = weight or (BOLD if bold else REGULAR)
    path = FONT_DIR / f"{FONT_FAMILY}-{name}.ttf"
    if not path.exists():
        raise FileNotFoundError(
            f"нет файла шрифта: {path}\n"
            f"положи семейство {FONT_FAMILY} в {FONT_DIR}"
        )
    # Не через ImageFont.truetype: он умеет искать шрифт по системным
    # папкам и возвращает свой класс. Путь у нас абсолютный и шрифт лежит
    # в репозитории, так что конструктор вызывается напрямую.
    return CachedFont(str(path), size)


def co2_color(ppm: int | None) -> tuple[int, int, int]:
    """Пороги под пересмотр: попадут в настройки дашборда (п.7)."""
    if ppm is None:
        return DIM
    if ppm < 800:
        return OK
    if ppm < 1400:
        return WARN
    return ALERT
