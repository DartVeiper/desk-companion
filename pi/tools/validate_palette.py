"""Проверка палитры графиков. Питоновский двойник validate_palette.js.

    py tools/validate_palette.py "#2a78d6,#eb6834,#1baf7a,#eda100" --mode light

Node на машине нет, а проверять палитру «на глаз» нельзя: различимость для
дальтоников — величина считаемая, а не вопрос вкуса. Пороги и матрицы взяты
из оригинала один в один, чтобы результаты совпадали.

Что считается:
  2. полоса светлоты   — OKLCH L в диапазоне режима
  3. минимум цветности — OKLCH C, ниже него цвет читается серым
  4. различимость при дальтонизме — OKLab dE x100 при симуляции протан/дейтан
  4b. различимость обычным зрением — та же dE без симуляции
  5. контраст к фону   — WCAG
"""

from __future__ import annotations

import argparse
import math

BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
CHROMA_FLOOR = 0.10
CVD_TARGET, CVD_FLOOR = 8.0, 6.0
NORMAL_FLOOR = 15.0
CONTRAST_MIN = 3.0
DEFAULT_SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}

# Machado, Oliveira & Fernandes (2009), тяжесть 1.0, в линейном RGB.
MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868),
               (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968),
               (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779),
               (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}


def to_linear(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)


def relative_luminance(hex_color: str) -> float:
    r, g, b = to_linear(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def oklab_from_linear(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklch(hex_color: str) -> tuple[float, float]:
    L, a, b = oklab_from_linear(to_linear(hex_color))
    return L, math.hypot(a, b)


def simulate(hex_color: str, kind: str) -> tuple[float, float, float]:
    r, g, b = to_linear(hex_color)
    m = MACHADO[kind]
    return tuple(min(1.0, max(0.0, m[i][0] * r + m[i][1] * g + m[i][2] * b)) for i in range(3))


def delta_e(h1: str, h2: str, kind: str | None = None) -> float:
    a = oklab_from_linear(simulate(h1, kind) if kind else to_linear(h1))
    b = oklab_from_linear(simulate(h2, kind) if kind else to_linear(h2))
    return 100 * math.dist(a, b)


def validate(palette: list[str], mode: str = "light",
             surface: str | None = None, pairs: str = "adjacent") -> int:
    surface = surface or DEFAULT_SURFACE[mode]
    low, high = BAND[mode]
    fails = 0

    print(f"\n  режим {mode}, фон {surface}, пар: {pairs}\n")
    print(f"  {'слот':5} {'цвет':9} {'L':>6} {'C':>6} {'контраст':>9}  вердикт")
    for i, color in enumerate(palette, 1):
        L, C = oklch(color)
        ratio = contrast(color, surface)
        notes = []
        if not (low <= L <= high):
            notes.append(f"L вне [{low}, {high}]")
        if C < CHROMA_FLOOR:
            notes.append(f"C < {CHROMA_FLOOR}")
        if notes:
            fails += 1
        if ratio < CONTRAST_MIN and not notes:
            notes.append("контраст < 3:1 — нужны подписи или таблица (WARN)")
        print(f"  {i:<5} {color:9} {L:6.3f} {C:6.3f} {ratio:8.2f}:1  "
              f"{'; '.join(notes) or 'ok'}")

    idx = ([(i, i + 1) for i in range(len(palette) - 1)] if pairs == "adjacent"
           else [(i, j) for i in range(len(palette)) for j in range(i + 1, len(palette))])

    print(f"\n  {'пара':11} {'протан':>8} {'дейтан':>8} {'тритан':>8} {'обычное':>9}  вердикт")
    for i, j in idx:
        a, b = palette[i], palette[j]
        p, d, t = (delta_e(a, b, k) for k in ("protan", "deutan", "tritan"))
        normal = delta_e(a, b)
        worst = min(p, d)
        if normal < NORMAL_FLOOR:
            verdict, bad = f"FAIL обычное < {NORMAL_FLOOR}", True
        elif worst < CVD_FLOOR:
            verdict, bad = f"FAIL CVD < {CVD_FLOOR}", True
        elif worst < CVD_TARGET:
            verdict, bad = "WARN — нужна вторая кодировка", False
        else:
            verdict, bad = "ok", False
        fails += bad
        print(f"  {i + 1}-{j + 1:<9} {p:8.1f} {d:8.1f} {t:8.1f} {normal:9.1f}  {verdict}")

    print(f"\n  провалов: {fails}\n")
    return fails


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("palette")
    parser.add_argument("--mode", default="light", choices=("light", "dark"))
    parser.add_argument("--surface")
    parser.add_argument("--pairs", default="adjacent", choices=("adjacent", "all"))
    args = parser.parse_args()

    colors = [c.strip() for c in args.palette.split(",") if c.strip()]
    raise SystemExit(1 if validate(colors, args.mode, args.surface, args.pairs) else 0)


if __name__ == "__main__":
    main()
