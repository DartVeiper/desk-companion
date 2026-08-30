"""Расчёты поверх activity_minute: стрик, сводки за день и за неделю.

Живут на Pi, а не в ПК-агенте: данные и так здесь, а считать раз в сутки
Zero 2 W хватает с большим запасом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .db import Database

#: Окно, в котором обязан быть перерыв (п.10 плана: «перерыв каждые 2 часа»).
WINDOW_HOURS = 2
#: Сколько минут подряд считается перерывом. Отойти на минуту за чаем —
#: не перерыв; порог заведомо занижен, чтобы стрик не рвался на ерунде.
BREAK_MINUTES = 5
#: Меньше этого за день — день не засчитывается ни в стрик, ни в пропуск.
#: Иначе выходные, когда за компьютером почти не сидели, рвали бы цепочку.
MIN_ACTIVE_MINUTES = 60
#: Сколько пустых дней подряд стрик переживает: длинные выходные — да,
#: отпуск — нет.
MAX_IDLE_DAYS = 3

CATEGORIES = ("code", "game", "browser", "other")


@dataclass
class DaySummary:
    day: date
    at_desk_minutes: int = 0
    keystrokes: int = 0
    mouse_clicks: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_hour: list[int] = field(default_factory=lambda: [0] * 24)
    had_breaks: bool = False
    counted: bool = False  # хватило активности, чтобы день вообще учитывать
    #: Самая длинная непрерывная сидка. had_breaks говорит «да или нет»,
    #: а дашборду нужно показать, насколько именно пересидели.
    longest_sitting_minutes: int = 0


def day_summary(db: Database, day: date) -> DaySummary:
    rows = db.activity_for_day(day)
    out = DaySummary(day=day, by_category=dict.fromkeys(CATEGORIES, 0))

    for row in rows:
        if not row["at_desk"]:
            continue
        ts = datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S")
        out.at_desk_minutes += 1
        out.by_hour[ts.hour] += 1
        out.keystrokes += row["keystrokes"] or 0
        out.mouse_clicks += row["mouse_clicks"] or 0
        category = row["category"] or "other"
        out.by_category[category] = out.by_category.get(category, 0) + 1

    out.counted = out.at_desk_minutes >= MIN_ACTIVE_MINUTES
    out.longest_sitting_minutes = longest_session_minutes(rows)
    out.had_breaks = 0 < out.longest_sitting_minutes < WINDOW_HOURS * 60
    return out


def longest_session_minutes(rows) -> int:
    """Самая длинная непрерывная работа за день, в минутах.

    Окно скользящее, а не привязанное к часам. Привязанное обманывает:
    сессия 12:10-14:00 полностью закрывает окно 12:00-14:00 и выглядит
    нарушением, хотя перерыв был прямо перед ней.

    Отлучки короче BREAK_MINUTES сессию не разрывают — отойти на минуту
    за чаем это не перерыв.
    """
    if not rows:
        return 0

    at_desk = {}
    for row in rows:
        ts = datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S")
        at_desk[ts.hour * 60 + ts.minute] = bool(row["at_desk"])

    longest = session = gap = 0
    for minute in range(24 * 60):
        if at_desk.get(minute, False):
            if gap and gap < BREAK_MINUTES:
                session += gap  # короткая отлучка — та же сессия
            gap = 0
            session += 1
            longest = max(longest, session)
        else:
            gap += 1
            if gap >= BREAK_MINUTES:
                session = 0
    return longest


def has_breaks_every_window(rows) -> bool:
    """Ни одной непрерывной сессии длиннее двух часов."""
    return 0 < longest_session_minutes(rows) < WINDOW_HOURS * 60


def streak_days(db: Database, today: date | None = None) -> int:
    """Дней подряд с перерывом в каждом двухчасовом окне.

    Считаем назад от вчера: сегодняшний день ещё не закончился, и рвать
    стрик из-за того, что перерыв будет вечером, неправильно. Сегодня
    добавляется к цепочке, только если условие уже выполнено.
    """
    today = today or date.today()
    count = 0

    summary = day_summary(db, today)
    if summary.counted and summary.had_breaks:
        count += 1

    day = today - timedelta(days=1)
    idle_run = 0
    for _ in range(365):
        summary = day_summary(db, day)
        if not summary.counted:
            # Пустой день цепочку не рвёт и не продлевает. Но подряд их
            # терпим немного: иначе двухнедельный отпуск сохранял бы
            # прошлогодний стрик живым до бесконечности.
            idle_run += 1
            if idle_run > MAX_IDLE_DAYS:
                break
            day -= timedelta(days=1)
            continue
        idle_run = 0
        if not summary.had_breaks:
            break
        count += 1
        day -= timedelta(days=1)
    return count


@dataclass
class WeekSummary:
    days: list[DaySummary]
    total_minutes: int
    by_category: dict[str, int]
    previous_total_minutes: int

    @property
    def delta_minutes(self) -> int:
        return self.total_minutes - self.previous_total_minutes


def week_summary(db: Database, end: date | None = None) -> WeekSummary:
    """Семь дней, заканчивая указанным, плюс предыдущая семёрка для сравнения."""
    end = end or date.today()
    days = [day_summary(db, end - timedelta(days=i)) for i in range(6, -1, -1)]

    by_category = dict.fromkeys(CATEGORIES, 0)
    for summary in days:
        for name, minutes in summary.by_category.items():
            by_category[name] = by_category.get(name, 0) + minutes

    previous = sum(
        day_summary(db, end - timedelta(days=i)).at_desk_minutes for i in range(13, 6, -1)
    )
    return WeekSummary(
        days=days,
        total_minutes=sum(d.at_desk_minutes for d in days),
        by_category=by_category,
        previous_total_minutes=previous,
    )
