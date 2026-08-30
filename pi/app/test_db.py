"""Тесты слоя данных и расчётов. Железо не нужно: py app/test_db.py"""

from __future__ import annotations

import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SCHEMA, Database  # noqa: E402
from app.stats import (  # noqa: E402
    day_summary,
    longest_session_minutes,
    streak_days,
    week_summary,
)

failed = 0
DAY = date(2026, 8, 19)


def check(name: str, got, expected) -> None:
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name:46} {got}")


def fresh() -> Database:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    return Database(tmp)


def fill(db: Database, busy: list[tuple[int, int]], day: date = DAY, category: str = "code") -> None:
    """busy — список отрезков (минута начала, минута конца) за столом."""
    at_desk = set()
    for begin, end in busy:
        at_desk.update(range(begin, end))
    rows = []
    for minute in range(24 * 60):
        stamp = datetime.combine(day, time.min) + timedelta(minutes=minute)
        present = minute in at_desk
        rows.append((stamp.strftime("%Y-%m-%d %H:%M:%S"),
                     40 if present else 0, 10 if present else 0,
                     500 if present else 0, int(present), category if present else ""))
    db.executemany_activity(rows)


print("Схема не разъехалась со скриптом установки")
setup = (Path(__file__).resolve().parents[1] / "setup-step1.sh").read_text(encoding="utf-8")


def tables(text: str) -> set[str]:
    """Имена таблиц и их столбцы — сравниваем состав, а не форматирование."""
    found = set()
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", text, re.S):
        name, body = match.group(1), match.group(2)
        columns = {line.strip().split()[0] for line in body.splitlines()
                   if line.strip() and not line.strip().startswith("--")}
        found.add((name, frozenset(columns)))
    return found


check("состав таблиц совпадает", tables(SCHEMA.read_text(encoding="utf-8")), tables(setup))

print("\nЗапись и чтение")
db = fresh()
now = datetime(2026, 8, 19, 14, 32)
db.add_activity_minute(now, 142, 38, 8420, True, "code")
db.add_activity_minute(now.replace(second=41), 99, 12, 1000, True, "game")
rows = db.activity_between(now - timedelta(minutes=1), now + timedelta(minutes=1))
check("повтор той же минуты перезаписал, а не задвоил", len(rows), 1)
check("перезапись взяла свежие данные", rows[0]["keystrokes"], 99)

db.add_env(now, 680, 23.4, 41.0)
check("последний замер воздуха читается", db.latest_env()["co2"], 680)
db.add_state_event(now, True, "rider64.exe", "code", False, None)
check("событие записалось", len(db.recent_events()), 1)

print("\nПравило перерывов: скользящее окно")
db = fresh()
fill(db, [(600, 700), (720, 820)])
check("две сессии по 100 мин с перерывом 20", longest_session_minutes(db.activity_for_day(DAY)), 100)
check("день засчитан", day_summary(db, DAY).had_breaks, True)

db = fresh()
fill(db, [(600, 700), (702, 820)])
check("отлучка на 2 мин сессию не рвёт", longest_session_minutes(db.activity_for_day(DAY)), 220)
check("день не засчитан", day_summary(db, DAY).had_breaks, False)

db = fresh()
fill(db, [(730, 850)])
check("ровно 120 мин — уже нарушение", day_summary(db, DAY).had_breaks, False)
db = fresh()
fill(db, [(730, 849)])
check("119 мин — ещё нормально", day_summary(db, DAY).had_breaks, True)

print("\nСводка за день")
db = fresh()
fill(db, [(600, 660)], category="code")
fill(db, [(800, 830)], day=DAY, category="game")
summary = day_summary(db, DAY)
check("минуты за столом посчитаны", summary.at_desk_minutes, 30)
check("разбивка по часам", summary.by_hour[13], 30)
check("мало активности — день не в зачёт", summary.counted, False)

print("\nСтрик")
db = fresh()
for i in range(5):
    fill(db, [(600, 700), (720, 820)], day=DAY - timedelta(days=i))
check("пять хороших дней подряд", streak_days(db, DAY), 5)

fill(db, [(600, 820)], day=DAY - timedelta(days=3))
check("нарушение в середине обрывает цепочку", streak_days(db, DAY), 3)

db = fresh()
fill(db, [(600, 700), (720, 820)], day=DAY)
fill(db, [(600, 700), (720, 820)], day=DAY - timedelta(days=2))
check("пустой день цепочку не рвёт", streak_days(db, DAY), 2)

print("\nСводка за неделю")
db = fresh()
for i in range(14):
    length = 100 if i < 7 else 60
    fill(db, [(600, 600 + length)], day=DAY - timedelta(days=i))
week = week_summary(db, DAY)
check("семь дней в сводке", len(week.days), 7)
check("итог за неделю", week.total_minutes, 700)
check("прошлая неделя", week.previous_total_minutes, 420)
check("разница со знаком", week.delta_minutes, 280)

print(f"\n  провалов: {failed}")
raise SystemExit(1 if failed else 0)
