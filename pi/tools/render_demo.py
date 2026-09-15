"""Демонстрация экранов: анимация карусели и общий лист.

    py tools/render_demo.py

Кладёт в docs/ две вещи:

  демо.gif        — карусель, как её видно на живом блоке
  все-экраны.png  — тот же набор одним листом, для беглого взгляда

Состояние здесь задано числами, а не берётся с платы. Демонстрация должна
собираться одинаково в любой день и на любой машине — иначе картинка в
README начнёт зависеть от того, проветрено ли в комнате.

Числа при этом настоящие по смыслу: 780 ppm — это «пора приоткрыть окно»,
а не круглая выдумка, и погода с температурой взяты из обычного сентября.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import forecast  # noqa: E402
from app import lang  # noqa: E402
from app import theme  # noqa: E402
from app.screens import registry  # noqa: E402
from app.state import Env, State, Weather  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs"
CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"

#: Сколько держать каждый экран, миллисекунд. Две секунды — столько нужно,
#: чтобы прочитать содержимое, но не заскучать.
HOLD_MS = 2000


def demo_state() -> State:
    """Правдоподобный вечер за работой."""
    now = datetime(2026, 9, 14, 19, 42)
    state = State(
        now=now,
        env=Env(co2=780, temperature=24.3, humidity=46, updated=now),
        weather=Weather(temp=14.6, cond="малооблачно", code=1, is_day=False),
    )
    state.weather.rain_soon_minutes = None
    # Прогноз на вечер и завтра. Без него экран прогноза показывал
    # «Прогноза нет» — то есть витрина проекта рекламировала отказ.
    state.weather.ahead = [
        forecast.Part("вечером", "сегодня", 14.0, 1, False),
        forecast.Part("ночью", "завтра", 9.0, 3, False),
        forecast.Part("утром", "завтра", 12.0, 61, True),
    ]
    # Часы сверены. Иначе в углу каждого кадра висело предупреждение
    # «время не сверено»: у Pi нет часов реального времени, и по умолчанию
    # блок честно не верит своим, пока не сверится по сети. На живом
    # устройстве это длится секунды, а на картинке висело всегда.
    state.health.clock_synced = True

    state.desk.presence = True
    state.desk.presence_since = now - timedelta(hours=1, minutes=12)
    state.desk.streak_days = 9

    pc = state.pc
    pc.last_heartbeat = now
    pc.active_app, pc.category = "Code", "code"
    pc.keystrokes, pc.mouse_clicks = 9124, 431
    pc.cpu_load, pc.cpu_temp = 34.0, 52.0
    pc.gpu_load, pc.gpu_temp = 71.0, 61.0
    pc.audio_active = True
    pc.track_artist, pc.track_title, pc.track_playing = "Kai Angel", "andy warhol", True
    return state


def frames() -> list[tuple[str, Image.Image]]:
    config = registry.load_config(CONFIG)
    state = demo_state()
    out = []
    for entry in config["screens"]["enabled"]:
        screen = registry.instantiate(entry)
        image = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
        # Через ту же обёртку, что на устройстве, иначе картинка
        # показывала бы не то, что человек увидит на экране.
        screen.render(state, lang.wrap(ImageDraw.Draw(image)), image)
        out.append((lang.t(screen.title or screen.name), image))
    return out


def sheet(shots: list[tuple[str, Image.Image]]) -> Image.Image:
    """Общий лист: все экраны сразу, с подписями."""
    cols = 2
    rows = -(-len(shots) // cols)
    pad, caption = 18, 34
    cell_w, cell_h = theme.WIDTH, theme.HEIGHT + caption
    canvas = Image.new("RGB", (cols * cell_w + pad * (cols + 1),
                               rows * cell_h + pad * (rows + 1)), (12, 12, 14))
    draw = ImageDraw.Draw(canvas)
    for index, (title, image) in enumerate(shots):
        x = pad + (index % cols) * (cell_w + pad)
        y = pad + (index // cols) * (cell_h + pad)
        canvas.paste(image, (x, y))
        draw.text((x + cell_w / 2, y + theme.HEIGHT + caption / 2), title,
                  font=theme.font(17), fill=(150, 156, 168), anchor="mm")
    return canvas


def main() -> None:
    # Английский набор кладём под латинскими именами, рядом с русским:
    # на английскую страницу нужны английские скриншоты, иначе она выглядит
    # недоделанной, а на русскую — русские.
    #     py tools/render_demo.py --en
    english = "--en" in sys.argv
    if english:
        lang.use("en")
    DOCS.mkdir(parents=True, exist_ok=True)
    shots = frames()

    # GIF держит палитру в 256 цветов, и приводить к ней каждый кадр
    # отдельно нельзя: палитры разойдутся, и на переходах пойдут цветные
    # разводы. Поэтому считаем одну общую по первому кадру.
    palette = shots[0][1].convert("P", palette=Image.ADAPTIVE, colors=128)
    converted = [image.quantize(palette=palette, dither=Image.Dither.NONE)
                 for _, image in shots]
    gif = DOCS / ("demo.gif" if english else "демо.gif")
    converted[0].save(gif, save_all=True, append_images=converted[1:],
                      duration=HOLD_MS, loop=0, optimize=True)

    board = DOCS / ("all-screens.png" if english else "все-экраны.png")
    sheet(shots).save(board)

    print(f"\n  {gif.name}  — {len(shots)} кадров, "
          f"{gif.stat().st_size / 1024:.0f} КБ")
    print(f"  {board.name}  — {board.stat().st_size / 1024:.0f} КБ\n")
    for title, _ in shots:
        print(f"    {title}")
    print()


if __name__ == "__main__":
    main()
