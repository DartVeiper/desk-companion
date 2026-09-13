"""Тесты частичной перерисовки и драйвера. Железо не нужно:

    py app/display/test_display.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import theme
from app.display.banded import (BandedDisplay, band_bounds, changed_runs,
                                changed_tiles, to_rgb565)
from app.display.st7796s import CASET, RAMWR, RASET, St7796sDisplay
from app.screens.clock import ClockScreen
from app.state import Env, State, Weather

failed = 0


def check(name: str, got, expected) -> None:
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name:46} {got}")


class FakeDisplay(BandedDisplay):
    """Копит переданные окна вместо отправки по шине."""

    width, height = theme.WIDTH, theme.HEIGHT

    def __init__(self) -> None:
        super().__init__()
        self.windows: list[tuple[int, int, int, int, int]] = []

    def write_window(self, x0, y0, x1, y1, payload) -> None:
        self.windows.append((x0, y0, x1, y1, len(payload)))


def solid(color) -> Image.Image:
    return Image.new("RGB", (theme.WIDTH, theme.HEIGHT), color)


print("Перегон в RGB565")
one = to_rgb565(Image.new("RGB", (1, 1), (255, 0, 0)))
check("чистый красный", hex(int(one[0, 0])), "0xf800")
check("чистый зелёный", hex(int(to_rgb565(Image.new("RGB", (1, 1), (0, 255, 0)))[0, 0])), "0x7e0")
check("чистый синий", hex(int(to_rgb565(Image.new("RGB", (1, 1), (0, 0, 255)))[0, 0])), "0x1f")
check("чёрный", int(to_rgb565(Image.new("RGB", (1, 1), (0, 0, 0)))[0, 0]), 0)
check("белый", hex(int(to_rgb565(Image.new("RGB", (1, 1), (255, 255, 255)))[0, 0])), "0xffff")
check("форма массива", to_rgb565(solid((0, 0, 0))).shape, (theme.HEIGHT, theme.WIDTH))

print("\nГраницы полос")
check("16 полос по 20 строк", band_bounds(320, 16), [(i * 20, i * 20 + 20) for i in range(16)])
check("высота не делится нацело", band_bounds(50, 16)[-1], (48, 50))
check("полос не больше запрошенного", len(band_bounds(320, 16)), 16)

print("\nПоиск изменившихся кусков")
base = to_rgb565(solid((0, 0, 0)))
check("первый кадр — целиком", changed_runs(base, None), [(0, 320)])
check("ничего не изменилось", changed_runs(base, base), [])

one_band = base.copy()
one_band[40:60] = 0xFFFF
check("одна полоса", changed_runs(one_band, base), [(40, 60)])

two_apart = base.copy()
two_apart[0:20] = 0xFFFF
two_apart[200:220] = 0xFFFF
check("две несмежные — два куска", changed_runs(two_apart, base), [(0, 20), (200, 220)])

two_next = base.copy()
two_next[40:80] = 0xFFFF
check("две смежные склеены в один", changed_runs(two_next, base), [(40, 80)])

check("смена размера — целиком", changed_runs(base, np.zeros((10, 10), dtype=np.uint16)), [(0, 320)])

print("\nСетка плиток")
check("первый кадр — один прямоугольник во весь экран",
      changed_tiles(base, None), [(0, 0, 480, 320)])
check("ничего не изменилось", changed_tiles(base, base), [])

corner = base.copy()
corner[0:20, 0:60] = 0xFFFF
check("одна плитка", changed_tiles(corner, base), [(0, 0, 60, 20)])

wide = base.copy()
wide[0:20, 0:180] = 0xFFFF
check("три плитки в ряд склеены", changed_tiles(wide, base), [(0, 0, 180, 20)])

tall = base.copy()
tall[0:60, 60:120] = 0xFFFF
check("столбец из трёх рядов склеен", changed_tiles(tall, base), [(60, 0, 120, 60)])

узкий = base.copy()
узкий[100:140, 300:360] = 0xFFFF
check("узкий кусок справа — только он",
      changed_tiles(узкий, base), [(300, 100, 360, 140)])

print("\nПередача")
d = FakeDisplay()
d.show(solid((0, 0, 0)))
check("первый кадр ушёл целиком", d.windows[0][4], theme.WIDTH * theme.HEIGHT * 2)
check("полных кадров", d.full_frames, 1)

before = d.bytes_sent
d.show(solid((0, 0, 0)))
check("тот же кадр — ни байта", d.bytes_sent - before, 0)
check("окон не добавилось", len(d.windows), 1)

frame = solid((0, 0, 0))
ImageDraw.Draw(frame).rectangle((100, 100, 160, 130), fill=(255, 255, 255))
d.show(frame)
check("частичных кадров", d.partial_frames, 1)
check("координаты окна", d.windows[1][:4], (60, 100, 179, 139))
check("послано сильно меньше полного кадра",
      d.windows[1][4] < theme.WIDTH * theme.HEIGHT * 2 // 10, True)

d.invalidate()
before = d.bytes_sent
d.show(frame)
check("после invalidate уходит целиком",
      d.bytes_sent - before, theme.WIDTH * theme.HEIGHT * 2)

print("\nКадр драйвера")
sent: list[tuple[str, bytes]] = []
drv = St7796sDisplay(
    write=lambda payload: sent.append(("data" if dc[0] else "cmd", payload)),
    set_dc=lambda high: dc.__setitem__(0, high),
    chunk=64,
)
dc = [False]
drv.write_window(0, 0, 3, 1, b"\x00\x01" * 8)
check("порядок команд", [p[1][0] for p in sent if p[0] == "cmd"], [CASET, RASET, RAMWR])
check("окно по X", sent[1][1], bytes([0, 0, 0, 3]))
check("окно по Y", sent[3][1], bytes([0, 0, 0, 1]))

sent.clear()
drv.write_window(0, 0, 1, 1, b"\xab" * 200)
payload = b"".join(p[1] for p in sent if p[0] == "data")[8:]  # без аргументов окон
check("данные нарезаны по chunk", max(len(p[1]) for p in sent if p[0] == "data"), 64)
check("данные дошли целиком", len(payload), 200)

print("\nСколько экономит перерисовка полосами")
state = State(now=datetime(2026, 8, 19, 14, 32),
              env=Env(co2=680, temperature=23.4, humidity=41),
              weather=Weather(temp=18, cond="облачно"))
state.desk.presence = True
screen = ClockScreen()
measured = FakeDisplay()


def draw(moment: datetime) -> Image.Image:
    state.now = moment
    img = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
    screen.render(state, ImageDraw.Draw(img), img)
    return img


measured.show(draw(datetime(2026, 8, 19, 14, 32)))
full = measured.bytes_sent
measured.show(draw(datetime(2026, 8, 19, 14, 33)))
minute = measured.bytes_sent - full

check("полный кадр, байт", full, 307_200)
check("смена минуты дешевле полного кадра вдесятеро", minute * 10 < full, True)

# Для сравнения: сколько ушло бы, дели мы кадр только горизонтальными
# полосами. Цифры часов высотой 132 px занимают шесть полос из шестнадцати,
# поэтому одни полосы экономят втрое, а плитки — на порядок.
before, after = to_rgb565(draw(datetime(2026, 8, 19, 14, 32))), \
    to_rgb565(draw(datetime(2026, 8, 19, 14, 33)))
bands_only = sum((end - start) * theme.WIDTH * 2
                 for start, end in changed_runs(after, before))


def timing(size: int) -> str:
    return f"{size:>7} байт   {size * 8 / 32e6 * 1000:5.1f} мс"


print(f"\n  смена минуты на Режиме 1, шина 32 МГц:")
print(f"    полный кадр       {timing(full)}")
print(f"    только полосами   {timing(bands_only)}   в {full / bands_only:.1f} раза меньше")
print(f"    плитками          {timing(minute)}   в {full / minute:.1f} раза меньше")

print("\nКеш растров шрифта")

# Кеш обязан быть невидимым: тот же кадр, пиксель в пиксель. Проверка
# появилась после того, как первая версия кеша молча ломала привязку
# текста — часы рисовались с уехавшей за экран цифрой, тесты проходили,
# а заметить это можно было только глазами на живой плате.
#
# Проверяем на всех экранах и на двух разных состояниях: ошибка в ключе
# кеша проявляется именно на втором рисовании, первое всегда честное.


def without_cache(size: int, bold: bool = False, weight: str | None = None):
    name = weight or (theme.BOLD if bold else theme.REGULAR)
    return ImageFont.truetype(str(theme.FONT_DIR / f"{theme.FONT_FAMILY}-{name}.ttf"), size)


def render_all(font_fn) -> list[bytes]:
    saved, theme.font = theme.font, font_fn
    try:
        out = []
        # Последним повторяем первое время: кеш обязан отдать тот же кадр,
        # и именно на повторе проверяется, что ключ собран правильно.
        for moment in (datetime(2026, 8, 19, 14, 32), datetime(2026, 8, 19, 14, 33),
                       datetime(2026, 8, 19, 9, 5), datetime(2026, 8, 19, 14, 32)):
            state.now = moment
            image = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
            ClockScreen().render(state, ImageDraw.Draw(image), image)
            out.append(image.tobytes())
        return out
    finally:
        theme.font = saved


theme.font.cache_clear()
cached_frames = render_all(theme.font)
plain_frames = render_all(without_cache)

check("кеш не меняет картинку", cached_frames == plain_frames, True)
check("разное время — разные кадры", cached_frames[0] != cached_frames[1], True)
check("повтор времени даёт тот же кадр", cached_frames[0] == cached_frames[3], True)

clock_font = theme.font(theme.CLOCK, bold=True)
check("кеш вообще срабатывает", clock_font.hits > 0, True)
check("шрифт остаётся настоящим FreeTypeFont",
      isinstance(clock_font, ImageFont.FreeTypeFont), True)

print(f"\n  провалов: {failed}")
raise SystemExit(1 if failed else 0)
