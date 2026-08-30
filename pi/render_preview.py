"""Отрисовка экранов в PNG без Pi и без железа.

    py render_preview.py

Кладёт кадры в preview/. Это тот же код отрисовки, который потом побежит
на Pi — меняется только бэкенд дисплея.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PIL import ImageDraw

from app.display.preview import PreviewDisplay
from app.screens import registry
from app.state import Env, Pc, State, Weather

HERE = Path(__file__).parent
OUT = HERE / "preview"

# Сценарии проверяют вёрстку на крайних значениях, а не только на «красивых».
SCENARIOS: dict[str, State] = {
    "день": State(
        now=datetime(2026, 8, 19, 14, 32),
        env=Env(co2=680, temperature=23.4, humidity=41),
        weather=Weather(temp=18, cond="облачно"),
        pc=Pc(last_heartbeat=datetime(2026, 8, 19, 14, 32)),
    ),
    "духота-и-дождь": State(
        now=datetime(2026, 8, 19, 18, 5),
        env=Env(co2=1680, temperature=27.8, humidity=63),
        weather=Weather(temp=-7, cond="ливень", rain_soon_minutes=12),
        pc=Pc(last_heartbeat=datetime(2026, 8, 19, 18, 5)),
    ),
    "ночь-пк-офлайн": State(
        now=datetime(2026, 8, 20, 3, 47),
        env=Env(co2=1120, temperature=21.0, humidity=38),
        weather=Weather(temp=4, cond="ясно"),
    ),
    "пусто": State(now=datetime(2026, 8, 19, 9, 5)),
}
SCENARIOS["день"].desk.presence = True
SCENARIOS["духота-и-дождь"].desk.manual_status = "не беспокоить"


def main() -> None:
    reg = registry.load(HERE / "app" / "config.toml")
    display = PreviewDisplay()
    OUT.mkdir(exist_ok=True)

    for screen in reg.screens:
        for label, state in SCENARIOS.items():
            frame = display.new_frame()
            screen.render(state, ImageDraw.Draw(frame), frame)
            display.show(frame)
            path = OUT / f"{screen.name}--{label}.png"
            frame.save(path)
            print(f"  {path.relative_to(HERE)}")

    print(f"\n  экранов: {len(reg.screens)}, кадров: {display.frames_shown}")


if __name__ == "__main__":
    main()
