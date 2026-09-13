"""Наблюдение за датчиком воздуха в реальном времени.

    python3 tools/air_watch.py

Сервис останавливать НЕ нужно: читаем снимок состояния, который сервис и
так пишет раз в секунду. Часы при этом работают, и датчик трогает ровно
один процесс — как и должно быть с шиной I2C.

Зачем. Датчик дважды «умирал», и оба раза причина осталась спорной: сняли
плёнку — перестал работать, вернули и обесточили — заработал. Плёнка и
обесточивание менялись вместе, так что виновника по этим двум случаям не
определить.

Как выяснить. Запустить наблюдение, отметить момент, снять плёнку и
смотреть: если датчик умирает в ту же секунду — дело в снятии (скорее
всего в статике от отклеивания). Если переживает — дело было в питании,
а плёнка ни при чём.

Дальше, если умер: обесточить плату на минуту, НЕ возвращая плёнку. Ожил
без плёнки — вопрос закрыт, виновата статика, и плёнку можно снимать
насовсем, заземлив руку.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import status as status_mod  # noqa: E402

#: Датчик обновляется раз в пять секунд. Пятнадцать без изменения числа —
#: это уже не «не успел», а замолчал.
SILENT_AFTER = 15.0


def read() -> dict | None:
    try:
        return json.loads(status_mod.default_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> None:
    print("\n  Наблюдение за датчиком воздуха. Ctrl+C — стоп.\n", flush=True)
    print("  Отмечай моменты вслух в чат: «снимаю плёнку», «вернул» —")
    print("  по времени будет видно, совпало ли это с отказом.\n", flush=True)
    print(f"  {'время':8} {'CO2':>6} {'темп':>7} {'влажн':>7}  что происходит")
    print(f"  {'-' * 8} {'-' * 6} {'-' * 7} {'-' * 7}  {'-' * 30}", flush=True)

    previous: tuple | None = None
    last_change = time.monotonic()
    was_silent = False
    started = time.monotonic()

    while True:
        snapshot = read()
        if snapshot is None:
            print(f"  {time.strftime('%H:%M:%S')} снимок не читается — сервис остановлен?", flush=True)
            time.sleep(2)
            continue

        env = snapshot.get("env") or {}
        values = (env.get("co2"), env.get("temperature"), env.get("humidity"))
        healthy = (snapshot.get("health") or {}).get("scd41_ok")
        now = time.monotonic()

        if values != previous:
            silent_for = now - last_change
            note = "живой"
            if was_silent:
                note = f"ОЖИЛ после {silent_for:.0f} с молчания"
                was_silent = False
            elif previous is None:
                note = "первое значение"
            co2, temp, hum = values
            print(f"  {time.strftime('%H:%M:%S')} {co2 if co2 is not None else '—':>6} "
                  f"{temp:>6.1f}° {hum:>6.1f}%  {note}"
                  if temp is not None else
                  f"  {time.strftime('%H:%M:%S')} {'—':>6} {'—':>7} {'—':>7}  {note}", flush=True)
            previous = values
            last_change = now
        elif not was_silent and now - last_change > SILENT_AFTER:
            was_silent = True
            print(f"  {time.strftime('%H:%M:%S')} {'':>6} {'':>7} {'':>7}  "
                  f"ЗАМОЛЧАЛ — число не меняется {SILENT_AFTER:.0f} с", flush=True)
            if healthy:
                print("           (часы этого ещё не заметили: "
                      "им нужна минута, чтобы признать отказ)", flush=True)

        if now - started > 3600:
            print("\n  Час прошёл, останавливаюсь.\n", flush=True)
            return
        time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  остановлено\n", flush=True)
