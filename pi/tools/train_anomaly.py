"""Обучение модели необычных сессий — шаг 11 плана.

    py tools/train_anomaly.py --db preview/desk.db
    py tools/train_anomaly.py --db /var/lib/desk-companion/desk.db --days 21

ВАЖНО (п.10 плана): обучать нужно на **настоящих** данных, накопленных за
1-2 недели работы. На синтетике из tools/seed.py конвейер проверяется, но
получившаяся модель описывает генератор, а не ваши привычки, и Режим 6
будет реагировать не на то.

Скрипт печатает, что именно модель считает необычным. Если список выглядит
бессмысленно — данных мало либо они нерепрезентативны, и включать Режим 6
рано.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.anomaly import MIN_SAMPLES, AnomalyModel, windows_for
from app.db import Database

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "app" / "anomaly.pkl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучить модель необычных сессий")
    parser.add_argument("--db", default="preview/desk.db")
    parser.add_argument("--days", type=int, default=28, help="сколько дней истории брать")
    parser.add_argument("--out", default=str(DEFAULT_MODEL))
    parser.add_argument("--contamination", type=float, default=0.05,
                        help="ожидаемая доля необычных окон")
    args = parser.parse_args()

    db = Database(args.db)
    first, last = db.span()
    if first is None:
        print("\n  в базе нет данных\n")
        raise SystemExit(1)

    since = max(first.date(), last.date() - timedelta(days=args.days - 1))
    windows = windows_for(db, since, last.date())
    span_days = (last.date() - since).days + 1

    print(f"\n  история : {since} .. {last.date()}  ({span_days} дн.)")
    print(f"  окон    : {len(windows)}  (нужно от {MIN_SAMPLES})")

    if len(windows) < MIN_SAMPLES:
        print(f"\n  Мало данных. Копите ещё — при {len(windows)} окнах модель")
        print("  запомнит шум, а не привычки. Режим 6 пока не включайте.\n")
        raise SystemExit(1)
    if span_days < 14:
        print(f"\n  Внимание: всего {span_days} дней. П.10 плана просит 1-2 недели —")
        print("  модель обучится, но выводы будут шаткими.\n")

    model = AnomalyModel(contamination=args.contamination)
    if not model.fit(windows):
        print(f"  обучение не состоялось — {model.unavailable_reason}")
        raise SystemExit(1)

    flagged = [(w, model.score(w)[1]) for w in windows if model.score(w)[0]]
    print(f"  помечено: {len(flagged)} окон "
          f"({len(flagged) / len(windows) * 100:.0f}% при заданных "
          f"{args.contamination * 100:.0f}%)\n")
    for window, reason in flagged[-12:]:
        print(f"    {window.start:%d.%m %H:%M}  {reason}")

    path = Path(args.out)
    model.save(path)
    print(f"\n  модель сохранена: {path}")
    print("  сервис подхватит её при следующем запуске\n")


if __name__ == "__main__":
    main()
