"""Поиск замыканий между ножками — программная замена прозвонке мультиметром.

Три проверки:
  1. подтяжка вверх: ножка обязана читаться как 1. Читается 0 — посажена на землю
  2. подтяжка вниз:  ножка обязана читаться как 0. Читается 1 — посажена на питание
  3. выдаём 1 на одну ножку и читаем остальные: повторил кто-то — они спаяны вместе
"""
import time

import lgpio

# Свободные GPIO. 2,3 заняты I2C; 7-11 — SPI; 14,15 — UART: их держит ядро.
FREE = [4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]
NAMES = {
    17: "энкодер CLK", 27: "энкодер DT", 22: "энкодер SW",
    25: "экран DC", 24: "экран RESET", 18: "подсветка экрана",
}

h = lgpio.gpiochip_open(0)
bad = set()


def name(p):
    n = NAMES.get(p)
    return f"  ({n})" if n else ""


def read_all(pull):
    out = {}
    for p in FREE:
        lgpio.gpio_claim_input(h, p, pull)
        out[p] = lgpio.gpio_read(h, p)
        lgpio.gpio_free(h, p)
    return out


print("1. Подтяжка вверх — каждая ножка обязана читаться как 1")
for p, v in read_all(lgpio.SET_PULL_UP).items():
    if v != 1:
        print(f"   [!!] GPIO{p} читается 0 — ПОСАЖЕНА НА ЗЕМЛЮ{name(p)}")
        bad.add(p)
print(f"   замкнуто на землю: {len(bad)}")

print("2. Подтяжка вниз — каждая ножка обязана читаться как 0")
n_up = 0
for p, v in read_all(lgpio.SET_PULL_DOWN).items():
    if v != 0:
        print(f"   [!!] GPIO{p} читается 1 — ПОСАЖЕНА НА ПИТАНИЕ{name(p)}")
        bad.add(p)
        n_up += 1
print(f"   замкнуто на питание: {n_up}")

print("3. Попарно: выдаём 1 на одну и смотрим, кто повторил")
pairs = 0
test = [p for p in FREE if p not in bad]
for p in test:
    others = [q for q in test if q != p]
    for q in others:
        lgpio.gpio_claim_input(h, q, lgpio.SET_PULL_DOWN)
    lgpio.gpio_claim_output(h, p, 1)
    time.sleep(0.002)
    for q in others:
        if lgpio.gpio_read(h, q) == 1:
            print(f"   [!!] GPIO{p} и GPIO{q} СОЕДИНЕНЫ между собой")
            pairs += 1
    lgpio.gpio_free(h, p)
    for q in others:
        lgpio.gpio_free(h, q)
print(f"   спаянных пар найдено: {pairs}")

lgpio.gpiochip_close(h)
print()
print(f"ИТОГ: проверено {len(FREE)} ножек, проблемных {len(bad)}, пар {pairs}")
print("ЧИСТО — замыканий нет" if not bad and not pairs else "ЕСТЬ ЗАМЫКАНИЯ, смотри выше")
