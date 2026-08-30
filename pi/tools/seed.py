"""Генератор правдоподобных данных для отладки без железа.

    py tools/seed.py --days 21 --db preview/desk.db

Нужен потому, что п.10 плана требует накопить 1-2 недели наблюдений в
activity_minute, прежде чем включать Режим 6, а дашборд (п.7) без данных
не разработать вовсе. Синтетика не заменяет настоящие замеры — на ней
нельзя обучать финальную модель, — но позволяет писать и проверять всё,
что стоит поверх базы.

Данные детерминированы: одно и то же зерно даёт одну и ту же историю,
иначе тесты поверх неё плавали бы.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database
from app.stats import streak_days

CATEGORIES = ("code", "browser", "game", "other")


def blocks_for(day: date, rng: random.Random) -> list[tuple[time, time, str]]:
    """Куски дня за столом. Будни и выходные живут по-разному."""
    weekend = day.weekday() >= 5
    if weekend:
        plan = [(12, 30, 15, 0, "browser"), (16, 0, 19, 30, "game"), (21, 0, 23, 30, "game")]
    else:
        plan = [(9, 30, 13, 0, "code"), (14, 0, 18, 30, "code"), (20, 30, 23, 0, "game")]

    out = []
    for h0, m0, h1, m1, category in plan:
        if rng.random() < 0.15:
            continue  # иногда блока просто не было
        shift = rng.randint(-40, 40)
        start = datetime.combine(day, time(h0, m0)) + timedelta(minutes=shift)
        end = datetime.combine(day, time(h1, m1)) + timedelta(minutes=shift + rng.randint(-30, 30))
        if end <= start:
            continue
        out.append((start, end, category))
    return out


def minutes_for_day(day: date, rng: random.Random, marathon: bool) -> dict[datetime, tuple]:
    """Поминутный след за день: сидки, перерывы, ввод."""
    result: dict[datetime, tuple] = {}
    for start, end, category in blocks_for(day, rng):
        cursor = start
        while cursor < end:
            # Длина сидки до перерыва. В «марафонский» день специально
            # делаем подход длиннее двух часов — это ломает стрик.
            sitting = rng.randint(150, 220) if marathon else rng.randint(35, 95)
            chunk_end = min(cursor + timedelta(minutes=sitting), end)
            while cursor < chunk_end:
                intensity = rng.random()
                keys = int(rng.gauss(90, 45) * intensity) if category == "code" else int(rng.gauss(25, 20))
                clicks = int(rng.gauss(20, 15)) if category != "code" else int(rng.gauss(8, 6))
                result[cursor] = (
                    max(0, keys), max(0, clicks),
                    max(0, int(rng.gauss(4000, 2500))), 1, category,
                )
                cursor += timedelta(minutes=1)
            # Перерыв: 6-20 минут, чтобы он честно считался перерывом
            cursor += timedelta(minutes=rng.randint(6, 20))
    return result


def seed(db: Database, days: int, seed_value: int, today: date | None = None) -> dict:
    rng = random.Random(seed_value)
    today = today or date.today()

    rows, env_rows, events = [], [], []
    co2, airing = 520.0, 0
    stats = {"minutes": 0, "marathons": []}

    for offset in reversed(range(days)):
        day = today - timedelta(days=offset)
        # Примерно раз в пять дней — день без перерывов
        marathon = rng.random() < 0.2 and offset != 0
        if marathon:
            stats["marathons"].append(day.isoformat())
        trace = minutes_for_day(day, rng, marathon)
        stats["minutes"] += len(trace)

        prev_at_desk = None
        for minute in range(1440):
            ts = datetime.combine(day, time.min) + timedelta(minutes=minute)
            entry = trace.get(ts)
            at_desk = entry is not None
            if entry:
                rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), *entry))

            # CO2 растёт при людях и оседает без них. Скорость взята близкой
            # к реальной: один человек в небольшой закрытой комнате даёт
            # +100..300 ppm в час, а не +800, иначе датчик всё время упирался
            # бы в потолок и график был бы бесполезен.
            if airing:
                co2 -= rng.uniform(18, 30)
                airing -= 1
            else:
                co2 += rng.uniform(2.0, 5.0) if at_desk else -rng.uniform(0.6, 1.8)
                # Иногда открывают окно — раз в несколько часов присутствия.
                if at_desk and co2 > 900 and rng.random() < 0.004:
                    airing = rng.randint(15, 40)
            co2 = min(1850.0, max(430.0, co2))
            if minute % 10 == 0:
                env_rows.append((ts, int(co2),
                                 round(21.5 + co2 / 900 + rng.uniform(-0.3, 0.3), 1),
                                 round(38 + co2 / 260 + rng.uniform(-2, 2), 1)))

            if prev_at_desk is not None and at_desk != prev_at_desk:
                events.append((ts, at_desk, entry[4] if entry else ""))
            prev_at_desk = at_desk

    db.executemany_activity(rows)
    db.executemany_env(
        [(t.strftime("%Y-%m-%d %H:%M:%S"), c, temp, hum) for t, c, temp, hum in env_rows])
    db.executemany_events(
        [(t.strftime("%Y-%m-%d %H:%M:%S"), int(presence),
          {"code": "rider64.exe", "game": "cs2.exe", "browser": "chrome.exe"}.get(category, ""),
          category, int(category == "game"), None) for t, presence, category in events])
    # Без слива журнала база на диске выглядит почти пустой: свежие записи
    # лежат в WAL, и размер файла вводит в заблуждение.
    db.checkpoint()
    stats["env"] = len(env_rows)
    stats["events"] = len(events)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Заполнить базу правдоподобными данными")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--db", default="preview/desk.db")
    parser.add_argument("--seed", type=int, default=20260819)
    args = parser.parse_args()

    path = Path(args.db)
    if path.exists():
        path.unlink()
    storage = Database(path)
    stats = seed(storage, args.days, args.seed)

    print(f"\n  база     : {path}  ({path.stat().st_size / 1024:.0f} КБ)")
    print(f"  дней     : {args.days}")
    print(f"  минут    : {stats['minutes']}")
    print(f"  замеров  : {stats['env']}")
    print(f"  событий  : {stats['events']}")
    print(f"  марафоны : {', '.join(stats['marathons']) or 'нет'}")
    print(f"  стрик    : {streak_days(storage)} дней\n")


if __name__ == "__main__":
    main()
