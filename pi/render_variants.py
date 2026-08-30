"""Черновики оформления Режима 1 рядом друг с другом.

    py render_variants.py

Кладёт каждый вариант отдельным PNG в родном 480x320 плюс два листа для
сравнения: перестановки одних и тех же блоков (А-Г) и концептуально
разные подходы (Д-З). Файл временный — уйдёт вместе с черновиками,
когда стиль будет выбран.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from app import theme
from app.screens.ambient import BigDigitsAmbient, MatrixAmbient, NightRedAmbient
from app.screens.clock import ClockScreen
from app.screens.clock_concepts import AnalogClock, GaugeClock, HybridClock, RingsClock
from app.screens.clock_variants import CardsClock, MinimalClock, SplitClock
from app.state import Env, Pc, State, Weather

HERE = Path(__file__).parent
OUT = HERE / "preview" / "variants"

GAP, LABEL_H = 12, 28

_base = ClockScreen()
_base.title = "А - модерн (текущий)"

GROUPS = {
    "компоновки": [_base, CardsClock(), SplitClock(), MinimalClock()],
    "концепты": [HybridClock(), RingsClock(), AnalogClock(), GaugeClock()],
    "покой": [NightRedAmbient(), BigDigitsAmbient(), MatrixAmbient()],
}


def scenario() -> State:
    state = State(
        now=datetime(2026, 8, 19, 14, 32),
        env=Env(co2=680, temperature=23.4, humidity=41),
        weather=Weather(temp=18, cond="облачно"),
        pc=Pc(last_heartbeat=datetime(2026, 8, 19, 14, 32)),
    )
    state.desk.presence = True
    return state


def sheet(frames: list[tuple[str, Image.Image]], cols: int = 2) -> Image.Image:
    rows = (len(frames) + cols - 1) // cols
    canvas = Image.new(
        "RGB",
        (GAP + cols * (theme.WIDTH + GAP), GAP + rows * (theme.HEIGHT + LABEL_H + GAP)),
        (0, 0, 0),
    )
    draw = ImageDraw.Draw(canvas)
    for i, (title, frame) in enumerate(frames):
        x = GAP + (i % cols) * (theme.WIDTH + GAP)
        y = GAP + (i // cols) * (theme.HEIGHT + LABEL_H + GAP)
        draw.text((x + 2, y + LABEL_H // 2), title,
                  font=theme.font(16, bold=True), fill=theme.FG, anchor="lm")
        canvas.paste(frame, (x, y + LABEL_H))
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    state = scenario()

    for group, screens in GROUPS.items():
        frames = []
        for screen in screens:
            frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
            screen.render(state, ImageDraw.Draw(frame), frame)
            frame.save(OUT / f"{screen.name}.png")
            frames.append((screen.title, frame))
            print(f"  {screen.title}")
        path = OUT / f"_сравнение-{group}.png"
        sheet(frames).save(path)
        print(f"  -> {path.name}\n")


if __name__ == "__main__":
    main()
