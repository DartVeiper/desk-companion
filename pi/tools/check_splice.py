"""Прозвонка самодельного тройника, не трогая мультиметр.

    python3 tools/check_splice.py            # ножки 7 и 29 по умолчанию
    python3 tools/check_splice.py 7 29 31    # свои ножки

Тройник — соединение, сделанное руками, и проверять его надо до того, как
он окажется в схеме. Иначе неработающий тач означает сразу три возможные
причины: плохая скрутка, промах мимо ножки или мёртвый модуль.

Суть: подаём на одну ножку то ноль, то единицу, а на остальных смотрим,
повторяют ли они. Повторяют — соединение есть.

Втыкать концы тройника надо в СВОБОДНЫЕ ножки, которые ничем не заняты:
7, 29, 31, 32, 33, 35, 36, 37, 38, 40 — их список печатает сам скрипт.
"""

from __future__ import annotations

import sys
import time

# Физическая ножка -> GPIO. Только свободные: занятые под экран, энкодер,
# радар и датчик воздуха сюда не попадают намеренно.
FREE = {7: 4, 29: 5, 31: 6, 32: 12, 33: 13, 35: 19, 36: 16, 37: 26, 38: 20, 40: 21}


def main() -> None:
    args = [int(a) for a in sys.argv[1:]] or [7, 29]
    if len(args) < 2:
        print("\n  нужны хотя бы две ножки: python3 tools/check_splice.py 7 29\n")
        raise SystemExit(2)

    unknown = [p for p in args if p not in FREE]
    if unknown:
        print(f"\n  ножки {unknown} заняты или не годятся.")
        print(f"  свободные: {', '.join(map(str, FREE))}\n")
        raise SystemExit(2)

    import lgpio

    driver, listeners = args[0], args[1:]
    handle = lgpio.gpiochip_open(0)

    print(f"\n  Подаю сигнал на ножку {driver}, слушаю {listeners}\n")

    lgpio.gpio_claim_output(handle, FREE[driver], 0)
    for pin in listeners:
        lgpio.gpio_claim_input(handle, FREE[pin], lgpio.SET_PULL_DOWN)

    followed = {pin: 0 for pin in listeners}
    rounds = 6
    for i in range(rounds):
        level = i % 2
        lgpio.gpio_write(handle, FREE[driver], level)
        time.sleep(0.05)
        for pin in listeners:
            if lgpio.gpio_read(handle, FREE[pin]) == level:
                followed[pin] += 1

    lgpio.gpio_free(handle, FREE[driver])
    for pin in listeners:
        lgpio.gpio_free(handle, FREE[pin])
    lgpio.gpiochip_close(handle)

    ok = True
    for pin in listeners:
        got = followed[pin]
        if got == rounds:
            print(f"  [ok]  ножка {pin}: соединена с {driver}")
        elif got == 0:
            print(f"  [!!]  ножка {pin}: НЕТ СВЯЗИ с {driver}")
            ok = False
        else:
            # Самый неприятный исход: контакт есть, но плавающий. Такое
            # соединение работает на столе и отваливается в корпусе.
            print(f"  [!!]  ножка {pin}: связь рвётся — совпало {got} из {rounds}")
            ok = False

    print()
    if ok:
        print("  Тройник цел: все ветки соединены надёжно.\n")
    else:
        print("  Где-то нет контакта. Проверь, что жилки скручены плотно")
        print("  и что провод сидит в ножке до упора.\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
