"""Поиск необычных сессий — шаги 10-11 плана, Режим 6.

Модель живёт на Pi, а не в ПК-агенте: данные и так здесь, в ML.NET нет
IsolationForest, а гонять раз в час Zero 2 W хватает с запасом.

Важная оговорка из п.10: **обучать можно только на настоящих данных**,
накопленных за 1-2 недели. Синтетика из tools/seed.py годится, чтобы
проверить, что конвейер собран правильно, но модель на ней описывает
генератор, а не вас.

Признаки берутся не поминутно, а по двухчасовым окнам. Минута слишком
мелкая единица: в ней всё равно либо сидишь, либо нет, и «необычность»
отдельной минуты ничего не значит. Окно же ловит то, ради чего Режим 6
и задуман: марафон без перерывов, ночное сидение, всплеск ввода.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import lang
from .db import Database
from .stats import BREAK_MINUTES

WINDOW_MINUTES = 120
#: Меньше этого в окне — человек просто отсутствовал, окно не признак.
MIN_WINDOW_MINUTES = 20
#: Доля наблюдений, которые модель считает выбросами. 5% — обычный выбор:
#: реже — модель молчит всегда, чаще — кричит на каждую вторую сессию.
CONTAMINATION = 0.05
#: Минимум окон для обучения. Меньше — модель запоминает шум, а не привычки.
MIN_SAMPLES = 60

FEATURE_NAMES = (
    "минут за столом", "нажатий в минуту", "кликов в минуту",
    "самая длинная сидка", "перерывов", "час суток", "выходной",
)


@dataclass
class Window:
    """Двухчасовое окно, приведённое к числам."""

    start: datetime
    at_desk_minutes: int
    keys_per_minute: float
    clicks_per_minute: float
    longest_sitting: int
    breaks: int
    hour: int
    weekend: int

    def vector(self) -> list[float]:
        # Час суток кодируем не числом, а точкой на окружности: иначе 23 и 0
        # оказываются максимально далеки друг от друга, хотя это соседние часы.
        angle = 2 * math.pi * self.hour / 24
        return [
            float(self.at_desk_minutes), self.keys_per_minute, self.clicks_per_minute,
            float(self.longest_sitting), float(self.breaks),
            math.sin(angle), math.cos(angle), float(self.weekend),
        ]


def windows_for(db: Database, since: date, until: date) -> list[Window]:
    """Нарезать историю на окна. Пустые окна пропускаем."""
    rows = db.activity_between(datetime.combine(since, datetime.min.time()),
                               datetime.combine(until + timedelta(days=1), datetime.min.time()))
    if not rows:
        return []

    buckets: dict[datetime, list] = {}
    for row in rows:
        if not row["at_desk"]:
            continue
        ts = datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S")
        key = ts.replace(minute=0, second=0, microsecond=0)
        key = key.replace(hour=key.hour - key.hour % (WINDOW_MINUTES // 60))
        buckets.setdefault(key, []).append((ts, row["keystrokes"] or 0, row["mouse_clicks"] or 0))

    out = []
    for start, entries in sorted(buckets.items()):
        if len(entries) < MIN_WINDOW_MINUTES:
            continue
        entries.sort()
        stamps = [e[0] for e in entries]
        longest = breaks = 0
        run_start = previous = stamps[0]
        for ts in stamps[1:]:
            if (ts - previous).total_seconds() / 60 >= BREAK_MINUTES:
                breaks += 1
                longest = max(longest, int((previous - run_start).total_seconds() // 60) + 1)
                run_start = ts
            previous = ts
        longest = max(longest, int((previous - run_start).total_seconds() // 60) + 1)

        minutes = len(entries)
        out.append(Window(
            start=start, at_desk_minutes=minutes,
            keys_per_minute=round(sum(e[1] for e in entries) / minutes, 2),
            clicks_per_minute=round(sum(e[2] for e in entries) / minutes, 2),
            longest_sitting=longest, breaks=breaks,
            hour=start.hour, weekend=int(start.weekday() >= 5),
        ))
    return out


# Как называть отклонение каждого признака: (поле, «больше обычного»,
# «меньше обычного»). None — про эту сторону говорить нечего.
_PHRASES = (
    ("longest_sitting", "{value:.0f} мин без перерыва", None),
    ("breaks", None, "почти без перерывов"),
    ("clicks_per_minute", "втрое больше мыши, чем обычно", None),
    ("keys_per_minute", "необычно много ввода", "сидит, но почти не печатает"),
    ("at_desk_minutes", "дольше обычного за столом", "заскочил ненадолго"),
)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def explain(window: Window, reference: list[Window], limit: int = 2) -> str:
    """Чем окно отличается от обычного.

    Считаем отклонение от медианы в единицах MAD — устойчивого разброса.
    Подогнанные вручную пороги здесь не работают: на живых данных они либо
    молчат, либо срабатывают всегда, а «необычная сессия» без причины
    ничего не подсказывает (п.10 требует, чтобы причина была).
    """
    if not reference:
        return lang.t("нет с чем сравнивать")

    scored = []
    for field, high, low in _PHRASES:
        values = [getattr(w, field) for w in reference]
        centre = _median(values)
        # MAD вместо стандартного отклонения: выбросы в обучающей выборке
        # не должны раздувать «норму», иначе аномалии перестают выделяться.
        spread = _median([abs(v - centre) for v in values]) or 1.0
        deviation = (getattr(window, field) - centre) / spread
        phrase = high if deviation > 0 else low
        if phrase and abs(deviation) >= 2:
            scored.append((abs(deviation),
                           lang.t(phrase).format(value=getattr(window, field))))

    if window.hour < 6 or window.hour >= 23:
        scored.append((99, lang.t("ночное время")))  # само по себе достаточный повод

    scored.sort(reverse=True)
    # Переводим куски до склейки: собранной фразы в словаре нет и быть не
    # может — сочетаний слишком много.
    return (", ".join(text for _, text in scored[:limit])
            or lang.t("непохоже на обычную сессию"))


class AnomalyModel:
    """IsolationForest поверх окон.

    sklearn импортируется внутри: на Pi он ставится отдельно, а весь код
    выше должен оставаться работоспособным и без него. Отсутствие библиотеки
    для нас не ошибка, а штатное состояние — до накопления данных Режим 6
    всё равно молчит, и ставить двести мегабайт заранее незачем.
    """

    #: Почему обучение не состоялось. Пусто — состоялось.
    unavailable_reason: str = ""

    def __init__(self, contamination: float = CONTAMINATION) -> None:
        self.contamination = contamination
        self.model = None
        self.reference: list[Window] = []
        self.trained_at: datetime | None = None
        self.samples = 0

    def fit(self, windows: list[Window]) -> bool:
        if len(windows) < MIN_SAMPLES:
            self.unavailable_reason = (
                f"мало данных: {len(windows)} окон из {MIN_SAMPLES}")
            return False

        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            # Ровно то, что обещано выше: без библиотеки отказываем, а не
            # роняем. Раньше здесь вылетало наружу, и сервис на плате без
            # scikit-learn упал бы в тот день, когда данных наконец хватит.
            self.unavailable_reason = (
                "scikit-learn не установлен: sudo bash setup-step2.sh --with-ml")
            return False

        self.unavailable_reason = ""

        self.model = IsolationForest(
            contamination=self.contamination,
            # Фиксированное зерно: иначе одни и те же данные дают разный
            # вердикт при каждом перезапуске сервиса.
            random_state=20260819,
            n_estimators=100,
        )
        self.model.fit([w.vector() for w in windows])
        self.reference = windows
        self.trained_at = datetime.now()
        self.samples = len(windows)
        return True

    def score(self, window: Window) -> tuple[bool, str]:
        """(аномалия ли, причина)."""
        if self.model is None:
            return False, "модель не обучена"
        flagged = self.model.predict([window.vector()])[0] == -1
        return flagged, explain(window, self.reference) if flagged else ""

    def save(self, path: Path) -> None:
        import pickle

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self.model))
        path.with_suffix(".json").write_text(json.dumps({
            "trained_at": self.trained_at.isoformat() if self.trained_at else None,
            "samples": self.samples,
            "contamination": self.contamination,
            "reference": [asdict(w) | {"start": w.start.isoformat()} for w in self.reference],
        }, ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path) -> bool:
        import pickle

        try:
            self.model = pickle.loads(path.read_bytes())
            meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError, pickle.UnpicklingError):
            return False
        self.samples = meta.get("samples", 0)
        self.trained_at = (datetime.fromisoformat(meta["trained_at"])
                           if meta.get("trained_at") else None)
        self.reference = [Window(**(w | {"start": datetime.fromisoformat(w["start"])}))
                          for w in meta.get("reference", [])]
        return True
