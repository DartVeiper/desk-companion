"""Тесты распознавания жестов. Железо не нужно: py inputs/test_gestures.py

Тач больше не решает, куда листать: тап уходит наружу с координатами, а
попал ли палец в карточку — разбирает Director (см. app/test_director.py).
Здесь проверяется только классификация самого жеста.

Пороги в gestures.py почти наверняка придётся подкрутить после первой
живой пробы на резистивной панели — тогда прогнать этот файл заново.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inputs.events import Action, EventBus
from inputs.gestures import GestureRecognizer

WIDTH = 480
CASES = [
    # (описание, точки, пауза между точками в мс, ожидание)
    ("свайп влево, резкий",       [(400, 160), (300, 165), (120, 170)],  20, Action.NEXT),
    ("свайп вправо, резкий",      [(80, 160), (200, 158), (380, 162)],   20, Action.PREV),
    ("свайп с рваным треком",     [(420, 160), (140, 175)],              30, Action.NEXT),
    ("медленный, но явный свайп", [(400, 160), (300, 160), (100, 160)], 300, Action.NEXT),
    ("диагональный увод",         [(400, 60), (130, 290)],               20, None),
    ("палец пролежал и поехал",   [(400, 160), (300, 160), (100, 160)], 700, None),
    ("тап",                       [(60, 200)],                            0, Action.TAP),
    ("тап с дрожанием пальца",    [(240, 200), (248, 206), (243, 203)],   5, Action.TAP),
    ("короткое нажатие",          [(240, 200), (241, 201)],             150, Action.TAP),
    ("удержание 0,8 с -> Manual", [(240, 200), (241, 201)],             800, Action.HOLD),
    ("удержание 3,2 с -> настройки", [(240, 200), (241, 201)],         3200, Action.SETTINGS),
]


def main() -> int:
    failed = 0
    for name, points, gap_ms, expected in CASES:
        bus = EventBus()
        rec = GestureRecognizer(bus, WIDTH)
        rec.on_down(*points[0])
        for point in points[1:]:
            time.sleep(gap_ms / 1000.0)
            rec.on_move(*point)
        if gap_ms and len(points) == 1:
            time.sleep(gap_ms / 1000.0)
        rec.on_up()

        event = bus.poll()
        got = event.action if event else None
        ok = got is expected
        # У тапа обязаны доехать координаты — без них Director не поймёт,
        # в какую карточку попал палец.
        if ok and expected is Action.TAP and (event.x, event.y) != points[0]:
            ok = False
            got = f"TAP без координат ({event.x}, {event.y})"
        if not ok:
            failed += 1
        exp_txt = expected.name if expected else "игнор"
        got_txt = got.name if hasattr(got, "name") else (got or "игнор")
        print(f"  [{'ok  ' if ok else 'FAIL'}] {name:30} ждём {exp_txt:8} получили {got_txt}")

    print(f"\n  {len(CASES) - failed} из {len(CASES)} прошло")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
