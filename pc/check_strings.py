"""Полнота переводов приложения для ПК.

    py pc/check_strings.py

Сверяет словари в pc/DeskCompanion/Strings с русским, исходным:

  - в каждом языке есть все русские ключи и нет лишних;
  - подстановки {0}, {1:0.0} в переводе те же, что в оригинале.

Вторая проверка важнее, чем кажется. Перевод, потерявший {1}, не падает —
string.Format молча выведет строку без числа, и «последняя копия: 14.09»
превратится в «last backup:». А перевод с лишним {2} падает с исключением,
но только когда до этой строки дойдёт дело, то есть у человека, а не в
сборке.

Разбором текста, а не сборкой: так проверка гоняется там же, где остальные
проверки проекта, — на любой машине с Python, без .NET.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "DeskCompanion" / "Strings"
ENTRY = re.compile(r'\["(\w+)"\]\s*=\s*"((?:[^"\\]|\\.)*)"')
SLOT = re.compile(r"\{(\d+)(?::[^}]*)?\}")


def table(path: Path) -> dict[str, str]:
    return dict(ENTRY.findall(path.read_text(encoding="utf-8")))


def slots(text: str) -> list[str]:
    return sorted(SLOT.findall(text))


def main() -> int:
    source = table(HERE / "Ru.cs")
    problems = 0
    others = sorted(p for p in HERE.glob("*.cs") if p.name != "Ru.cs")

    for path in others:
        target = table(path)
        missing = sorted(set(source) - set(target))
        extra = sorted(set(target) - set(source))
        mismatched = sorted(key for key in set(source) & set(target)
                            if slots(source[key]) != slots(target[key]))

        print(f"\n  {path.stem}: ключей {len(target)} из {len(source)}")
        for key in missing:
            print(f"    НЕТ ПЕРЕВОДА   {key}")
        for key in extra:
            print(f"    ЛИШНИЙ КЛЮЧ    {key}")
        for key in mismatched:
            print(f"    ПОДСТАНОВКИ    {key}: {slots(source[key])} -> {slots(target[key])}")
        problems += len(missing) + len(extra) + len(mismatched)

    if not others:
        print("\n  других языков, кроме русского, нет")

    print(f"\n  {'всё переведено' if not problems else f'проблем: {problems}'}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
