"""Из чего складывается задержка «датчик увидел → на экране видно».

    python3 tools/latency.py            # всё, что можно измерить здесь
    python3 tools/latency.py --no-spi   # без экрана, на машине разработки

Зачем. «Кажется, подтормаживает» — не диагноз, чинить по нему нечего.
Задержка складывается из четырёх слагаемых, и они отличаются в сто раз:
рисование кадра считается микросекундами, отправка по SPI — сотней
миллисекунд, а ожидание следующего опроса может съесть четверть секунды
на пустом месте. Пока не измерено, оптимизировать будешь не то.

Запускать с остановленным сервисом: экран и шина у нас одни на всех.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app import theme  # noqa: E402
from app.display import banded  # noqa: E402
from app.main import MAX_FRAME_GAP, Application  # noqa: E402
from app.display.preview import PreviewDisplay  # noqa: E402
from app.screens.registry import load_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "app" / "config.toml"
WIDTH, HEIGHT = 480, 320
REPEATS = 30


def timed(label: str, action, repeats: int = REPEATS) -> tuple[str, float, float]:
    """Среднее и худшее время вызова в миллисекундах.

    Худшее важнее среднего: рывок видно глазом, а среднее его прячет.
    """
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return label, statistics.median(samples), max(samples)


def render_costs(app: Application) -> list[tuple[str, float, float]]:
    """Во что обходится кадр до того, как он попал на шину."""
    out = []
    screen = app.director.current(app.state)
    frame = Image.new("RGB", (WIDTH, HEIGHT), theme.BG)

    def draw():
        image = Image.new("RGB", (WIDTH, HEIGHT), theme.BG)
        screen.render(app.state, ImageDraw.Draw(image), image)

    out.append(timed("сборка кадра (рисование)", draw))

    screen.render(app.state, ImageDraw.Draw(frame), frame)
    out.append(timed("перегон в RGB565", lambda: banded.to_rgb565(frame)))

    current = banded.to_rgb565(frame)
    previous = current.copy()
    previous[100:140, :] ^= 0xFFFF  # как будто сменилась одна полоса
    out.append(timed("поиск изменившихся полос",
                     lambda: banded.changed_runs(current, previous)))
    # Полосами кадр не отправляется — отправка режет его сеткой плиток,
    # и мерить надо именно её. Разница между этими двумя строчками и есть
    # цена того, что меряешь не то, что работает.
    out.append(timed("поиск изменившихся плиток",
                     lambda: banded.changed_tiles(current, previous)))
    return out


def spi_costs(config: dict) -> list[tuple[str, float, float]]:
    """Сколько стоит сама шина. Здесь и живёт основная задержка."""
    from app import hardware

    kit = hardware.build(config, None, WIDTH, HEIGHT, want={"display"})
    if kit.display is None:
        problems = "; ".join(kit.problems) or "неизвестно почему"
        print(f"  экран не поднялся ({problems}) — часть про SPI пропущена\n")
        return []

    display = kit.display
    full = Image.new("RGB", (WIDTH, HEIGHT), theme.BG)
    ImageDraw.Draw(full).rectangle((0, 0, WIDTH, HEIGHT), fill=(20, 30, 40))
    stripe = full.copy()
    ImageDraw.Draw(stripe).rectangle((0, 100, WIDTH, 140), fill=(200, 60, 60))

    out = []
    flip = [0]

    def full_frame():
        # Меняем содержимое каждый раз, иначе частичная перерисовка честно
        # ничего не пошлёт, и мы измерим скорость сравнения, а не шины.
        flip[0] ^= 1
        display.invalidate()
        display.show(full if flip[0] else stripe)

    def band_frame():
        flip[0] ^= 1
        display.show(stripe if flip[0] else full)

    out.append(timed("отправка по SPI: весь кадр", full_frame, 10))
    display.show(full)
    out.append(timed("отправка по SPI: одна полоса", band_frame, 10))
    display.close()
    return out


def loop_cost(app: Application) -> tuple[str, float, float]:
    """Холостой проход цикла: ничего не изменилось, рисовать нечего."""
    app.tick()  # первый проход всегда дорогой: прогрев кешей
    return timed("холостой проход цикла", app.tick, 200)


def budget(rows: dict[str, float], radar_interval: float, nap: float) -> None:
    """Сложить задержку пути «радар увидел → пиксели» по слагаемым.

    Модель повторяет устройство цикла: он спит до ближайшего срока опроса,
    поэтому отдельного «ждём шага цикла» больше нет — осталась только
    зернистость сна. Пауза между кадрами считается по стоимости самого
    кадра, и для частичной перерисовки это её собственное время.

    Слагаемые не пересекаются. Это не придирка: замер «отправка по SPI»
    делается через show(), а он внутри себя и переводит кадр в RGB565, и
    ищет изменившиеся плитки. Сложить его с отдельными замерами перегона
    и поиска — значит посчитать их дважды и отчитаться о результате лучше
    настоящего.
    """
    draw = rows.get("сборка кадра (рисование)", 0)
    send = rows.get("отправка по SPI: одна полоса", 0)
    frame = draw + send

    print("\n  путь «радар увидел → на экране видно», худший случай\n")
    parts = [
        ("ждём своей очереди опроса", radar_interval * 1000),
        ("зернистость сна цикла", nap * 1000),
        ("ждём окна после прошлого кадра", min(MAX_FRAME_GAP * 1000, frame)),
        ("рисуем экран", draw),
        ("переводим, сравниваем, шлём", send),
    ]
    for label, value in parts:
        print(f"    {label:32} {value:7.0f} мс")
    print(f"    {'':32} {'-' * 7}")
    print(f"    {'итого':32} {sum(v for _, v in parts):7.0f} мс")
    print(f"\n  из них работы {frame:.0f} мс, ожидания "
          f"{sum(v for _, v in parts) - frame:.0f} мс\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Где уходит время")
    parser.add_argument("--no-spi", action="store_true",
                        help="не трогать экран: только счёт и рисование")
    args = parser.parse_args()

    if not args.no_spi:
        import _service
        _service.require_stopped()

    config = load_config(CONFIG)
    app = Application(PreviewDisplay(ROOT / "preview" / "latency.png"), [],
                      config_path=CONFIG)

    print("\n  Где уходит время. Медиана и худшее из замеров.\n")
    print(f"  {'что меряем':36} {'медиана':>9} {'худшее':>9}")
    print(f"  {'-' * 36} {'-' * 9} {'-' * 9}")

    rows: dict[str, float] = {}
    measurements = [loop_cost(app), *render_costs(app)]
    if not args.no_spi:
        measurements += spi_costs(config)

    for label, median, worst in measurements:
        rows[label] = median
        print(f"  {label:36} {median:7.1f} мс {worst:7.1f} мс")

    from app.sources.sensors import Ld2410Source
    from app import main as main_mod

    budget(rows, Ld2410Source.interval, main_mod.MIN_NAP)


if __name__ == "__main__":
    main()
