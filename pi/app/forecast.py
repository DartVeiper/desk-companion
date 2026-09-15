"""Прогноз по частям суток — утро, день, вечер, ночь.

Почасовой прогноз на экране 480x320 не поместится и не нужен: человеку
важно не «сколько будет в 14:00», а «холодно ли будет вечером и брать ли
зонт». Поэтому часы сворачиваются в четыре привычные части суток.

Показываем только то, что впереди. Утро, которое уже прошло, занимает
место и не говорит ничего: в пять вечера полезны вечер, ночь и завтрашнее
утро, а не сегодняшнее.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Части суток: подпись, час начала, час конца (конец не включается).
PARTS = (
    ("ночью", 0, 6),
    ("утром", 6, 12),
    ("днём", 12, 18),
    ("вечером", 18, 24),
)

#: Насколько код погоды «важнее» другого. За часть суток показываем самое
#: значимое, а не самое частое: час грозы в спокойном вечере — это то,
#: ради чего на прогноз и смотрят, и усреднение его бы потеряло.
SEVERITY = {
    0: 0, 1: 1, 2: 2, 3: 3,
    45: 4, 48: 4,
    51: 5, 53: 5, 55: 6, 56: 6, 57: 6,
    61: 7, 63: 8, 65: 9, 66: 9, 67: 9,
    71: 7, 73: 8, 75: 9, 77: 8,
    80: 8, 81: 8, 82: 10,
    85: 9, 86: 10,
    95: 11, 96: 12, 99: 12,
}


def worst_code(codes: list[int]) -> int:
    """Самый значимый код из набора.

    Значимость, а не номер: у WMO снег (71) идёт после дождя (61), но по
    номеру ливень 82 обогнал бы грозу 95 только случайно. Порядок задан
    таблицей SEVERITY.
    """
    return max(codes, key=lambda code: SEVERITY.get(code, 0))


@dataclass
class Part:
    """Одна часть суток."""

    label: str
    #: «сегодня» или «завтра» — нужно, когда на экране обе.
    day: str
    temp: float
    code: int
    is_day: bool

    @property
    def title(self) -> str:
        from . import lang

        # Часть суток и слово «завтра» переводим по отдельности: в
        # английском порядок другой — «tomorrow evening», а не
        # «evening tomorrow», — и шаблон это учитывает.
        part = lang.t(self.label)
        return part if self.day == "сегодня" else lang.t("завтра {}").format(part)


def _part_of(hour: int) -> tuple[str, int, int]:
    for label, start, end in PARTS:
        if start <= hour < end:
            return label, start, end
    return PARTS[-1]


def parts(times: list[str], temps: list[float], codes: list[int],
          now: datetime, limit: int = 3) -> list[Part]:
    """Свернуть почасовой прогноз в части суток, начиная с текущей.

    Текущую часть берём целиком, а не с этого часа: «вечером +12» понятнее,
    чем «с 19:00 до 24:00 +12», а разница между ними — один-два градуса.
    """
    buckets: dict[tuple[int, str], list[tuple[float, int]]] = {}
    order: list[tuple[int, str]] = []

    for stamp, temp, code in zip(times, temps, codes):
        if temp is None or code is None:
            continue
        moment = datetime.fromisoformat(stamp)
        if moment < now.replace(minute=0, second=0, microsecond=0):
            continue
        label, start, _ = _part_of(moment.hour)
        key = ((moment.date() - now.date()).days, label)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append((float(temp), int(code)))

    out: list[Part] = []
    for key in order[:limit]:
        values = buckets[key]
        offset, label = key
        temperatures = sorted(value for value, _ in values)
        worst = worst_code([code for _, code in values])
        out.append(Part(
            label=label,
            day="сегодня" if offset == 0 else "завтра",
            # Медиана, а не среднее: один тёплый час в конце вечера не
            # должен делать весь вечер тёплым.
            temp=temperatures[len(temperatures) // 2],
            code=worst,
            is_day=label in ("утром", "днём"),
        ))
    return out

