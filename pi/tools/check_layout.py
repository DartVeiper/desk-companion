"""Ищет текст, вылезающий за края экрана и наезжающий на соседей.

    py tools/check_layout.py              # проверка всех экранов
    py tools/check_layout.py --render     # плюс PNG с подсветкой проблем

Глазами такое ловится плохо: строка вылезает только при длинном значении,
а длинное значение бывает не в тот день, когда смотришь. Поэтому подменяем
ImageDraw и записываем прямоугольник каждой надписи, а потом смотрим, что
с ними не так.

Проверяем три беды:
  за краем    — надпись выходит за 480x320, часть текста не видна вовсе
  наезд       — две надписи перекрываются больше чем на четверть площади
  в упор      — надпись подходит к краю ближе чем на три точки
"""

from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import lang  # noqa: E402
from app import theme  # noqa: E402
from app.screens import registry as registry_mod  # noqa: E402
from app import forecast
from app.state import State  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"
EDGE = 3          # ближе этого к краю — уже в упор
OVERLAP = 0.25    # доля площади, с которой наезд считается наездом


class Recorder(ImageDraw.ImageDraw):
    """ImageDraw, запоминающий, куда легла каждая надпись."""

    def __init__(self, im):
        super().__init__(im)
        self.texts: list[tuple[str, tuple[float, float, float, float]]] = []

    def text(self, xy, text, *args, **kwargs):  # noqa: A003
        super().text(xy, text, *args, **kwargs)
        if not str(text).strip():
            return
        try:
            box = self.textbbox(xy, str(text), font=kwargs.get("font"),
                                anchor=kwargs.get("anchor"))
        except (ValueError, TypeError):
            return
        self.texts.append((str(text), box))


def area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection(a, b) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def states() -> list[tuple[str, State]]:
    """Состояния, на которых вёрстку и рвёт: пусто, длинно, аварийно."""
    now = datetime(2026, 9, 13, 14, 30)

    empty = State(now=now)
    empty.desk.presence = True

    full = State(now=now)
    full.desk.presence = True
    full.desk.presence_since = now - timedelta(hours=6, minutes=48)
    full.env.co2, full.env.temperature, full.env.humidity = 1487, 23.7, 41
    full.env.updated = now
    full.weather.temp, full.weather.cond = -17.4, "сильный снегопад с метелью"
    full.weather.rain_soon_minutes = 35
    # Город теперь выбирается поиском по всем населённым пунктам мира, так
    # что в заголовок может приехать что угодно. Берём не длиннейшее на
    # планете, а честно длинное из тех, что человек правда выберет.
    full.weather.place = "Белгород-Днестровский"
    full.pc.last_heartbeat = now
    full.pc.active_app, full.pc.category = "Visual Studio Code — часы", "code"
    full.pc.keystrokes, full.pc.mouse_clicks = 18432, 9765
    full.pc.cpu_load, full.pc.gpu_load = 97.6, 99.9
    full.pc.cpu_temp, full.pc.gpu_temp = 88.4, 91.2
    full.pc.anomaly_flag = True
    full.pc.anomaly_reason = "необычно длинная сессия без перерывов, уже 4 ч 12 мин"
    # Название трека приходит из чужого плеера, и его длину мы не
    # выбираем: у живых релизов бывает и такое.
    full.pc.track_artist = "Оркестр имени Владимира Спивакова"
    full.pc.track_title = ("Симфония №7 до мажор, часть III — "
                           "Allegro molto vivace (концертная запись)")
    full.pc.track_playing = True
    # Три часа без перерыва: заголовок становится длиннее обычного «за
    # столом», а справа от него живёт «ПК офлайн».
    # История CO2 для спарклайна: без неё ветка с графиком не рисуется
    # вовсе, и наезд подписи на вердикт проверка не видит.
    full.env.trend = [560 + (i * 7) % 340 for i in range(48)]
    full.health.clock_synced = True
    # Прогноз с самыми длинными подписями: «завтра вечером» в узкой
    # колонке и «сильный снегопад с метелью» под ней.
    full.weather.ahead = [
        forecast.Part("вечером", "сегодня", -14.0, 75, False),
        forecast.Part("ночью", "завтра", -19.0, 96, False),
        forecast.Part("вечером", "завтра", -11.0, 82, False),
    ]
    full.desk.note_presence(True, now - timedelta(hours=3), 5)
    full.desk.manual_status = "не беспокоить"
    full.desk.manual_until = now + timedelta(hours=4, minutes=37)
    full.desk.streak_days = 128
    full.health.wifi_ssid = "OpenWrt-2.4G-Гостевая-Длинная"
    full.health.wifi_signal_dbm = -71
    full.health.ip = "192.168.100.237"
    full.health.uptime_seconds = 987654
    full.health.cpu_temp = 78.9
    full.health.disk_free_pct = 7.3
    full.brightness = 100

    broken = State(now=now)
    broken.desk.presence = False
    broken.health.wifi_ok = False
    broken.health.mqtt_ok = False
    broken.health.scd41_ok = False
    broken.health.ld2410_ok = False
    broken.health.throttled = True
    broken.health.disk_free_pct = 2.0
    broken.health.cpu_temp = 84.0
    broken.health.uptime_seconds = 61
    broken.brightness = 10

    # Четвёртое состояние: всё длинное, но музыка не играет. Нужно
    # потому, что строка под часами — единственная на два жильца: трек и
    # дата занимают одно место, и проверка с играющей музыкой ветку с
    # датой не трогает вовсе. Ровно так и проехал наезд даты на дождь.
    silent = copy.deepcopy(full)
    silent.pc.track_playing = False

    return [("пусто", empty), ("длинные значения", full),
            ("длинные значения без музыки", silent), ("всё сломано", broken)]


def main() -> None:
    render = "--render" in sys.argv
    # Английский проверять обязательно отдельно: слова другой длины,
    # и подпись, влезавшая по-русски, может уехать за край.
    #     py tools/check_layout.py --en
    if "--en" in sys.argv:
        lang.use("en")
        print("\n  язык: английский")
    config = registry_mod.load_config(CONFIG)

    entries = list(config["screens"]["enabled"])
    for key in ("manual", "settings"):
        if config["screens"].get(key):
            entries.append(config["screens"][key])
    entries += list(config.get("ambient", {}).get("enabled", []))

    screens = []
    for entry in entries:
        screen = registry_mod.instantiate(entry)
        screens.append(screen)
        screens.extend(getattr(screen, "details", []) or [])
    # Калибровка тача: в каталоге её нет, а подписи у неё длинные — итог
    # с причиной отказа первым кандидатом уехал бы за край.
    from check_language import calibration_screens
    screens.extend(calibration_screens())

    out_dir = Path(__file__).resolve().parents[1] / "preview" / "вёрстка"
    if render:
        out_dir.mkdir(parents=True, exist_ok=True)

    problems = 0
    checked = 0
    for screen in screens:
        for label, state in states():
            frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
            draw = Recorder(frame)
            try:
                # Через ту же обёртку, что и на устройстве: иначе на
                # английском проверялась бы русская вёрстка, то есть ничего.
                screen.render(state, lang.wrap(draw), frame)
            except Exception as exc:  # noqa: BLE001
                print(f"  [ПАДЕНИЕ] {screen.name} / {label}: "
                      f"{type(exc).__name__}: {exc}")
                problems += 1
                continue
            checked += 1

            bad: list[tuple[float, float, float, float]] = []
            for text, box in draw.texts:
                if box[0] < 0 or box[1] < 0 or box[2] > theme.WIDTH or box[3] > theme.HEIGHT:
                    print(f"  [ЗА КРАЕМ] {screen.name} / {label}: {text!r} -> "
                          f"{tuple(round(v) for v in box)}")
                    problems += 1
                    bad.append(box)
                elif (box[0] < EDGE or box[1] < EDGE
                      or box[2] > theme.WIDTH - EDGE or box[3] > theme.HEIGHT - EDGE):
                    print(f"  [В УПОР]   {screen.name} / {label}: {text!r} "
                          f"вплотную к краю")
                    problems += 1
                    bad.append(box)

            for i, (t1, b1) in enumerate(draw.texts):
                for t2, b2 in draw.texts[i + 1:]:
                    over = intersection(b1, b2)
                    if over > OVERLAP * min(area(b1), area(b2)) and over > 40:
                        print(f"  [НАЕЗД]    {screen.name} / {label}: "
                              f"{t1!r} и {t2!r}")
                        problems += 1
                        bad += [b1, b2]

            if render and bad:
                mark = ImageDraw.Draw(frame)
                for box in bad:
                    mark.rectangle(box, outline=(255, 70, 70), width=2)
                frame.save(out_dir / f"{screen.name}--{label}.png")

    print(f"\n  Проверено отрисовок: {checked}")
    print(f"  Проблем: {problems}\n")
    if render and problems:
        print(f"  кадры с подсветкой: {out_dir}\n")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
