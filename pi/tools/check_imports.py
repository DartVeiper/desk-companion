"""Проверка, что все импорты ведут к существующим именам.

    python3 tools/check_imports.py

Зачем этот инструмент появился. Файл hardware.py импортировал класс
PresenceSource, которого в проекте нет вовсе — настоящий класс зовётся
Ld2410Source. Радар из-за этого не поднимался ни разу за всё время: сборка
железа ловит ошибку каждого узла отдельно, чтобы недопаянный датчик не
ронял часы, и ImportError уходил в ту же корзину, что «датчик не припаян».

Почему не поймали тесты. Тесты импортируют Ld2410Source напрямую, по
правильному имени, и проходят. Путь через hardware.py они не трогают —
там нужен spidev, которого на машине разработки нет.

Почему не поймает линтер. Ни pyflakes, ни ruff не заглядывают в модуль,
из которого импортируют: для них `from .x import Y` — это просто имя Y,
появившееся в области видимости.

Что делает этот. Разбирает каждый файл в синтаксическое дерево (не
выполняя его, поэтому spidev и не нужен), находит все `from ... import`,
раскладывает путь до файла и смотрит, объявлено ли там такое имя. Секунда
работы, ноль зависимостей — и целый класс ошибок становится невозможен.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Чужой код проверять нечем и незачем: мы не знаем, что там объявлено,
#: а стандартная библиотека и без нас в порядке.
OURS = ("app", "tools")


def sources() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def bound_by(target: ast.expr) -> set[str]:
    """Какие имена создаёт левая часть присваивания.

    Распаковку кортежа приходится разбирать отдельно: строка вида
    `CASET, RASET, RAMWR = 0x2A, 0x2B, 0x2C` объявляет три имени сразу, и
    без этого проверка ругалась бы на совершенно здоровый код. Проверка,
    которая врёт, хуже отсутствующей — её перестают читать.
    """
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for item in target.elts:
            out |= bound_by(item)
        return out
    if isinstance(target, ast.Starred):
        return bound_by(target.value)
    return set()


def defined_names(path: Path) -> set[str]:
    """Имена, объявленные в модуле на верхнем уровне.

    Считаем объявлением всё, что создаёт имя: класс, функцию, присваивание,
    и в том числе импорт — реэкспорт через __init__.py тоже способ отдать
    имя наружу, и он в проекте используется.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names |= bound_by(target)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Try):
            # Необязательная зависимость: `try: import sklearn / except:`.
            # Имена из обеих веток настоящие, просто одно из них — заглушка.
            for branch in [node.body, node.orelse, *[h.body for h in node.handlers]]:
                for inner in branch:
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        for alias in inner.names:
                            names.add(alias.asname or alias.name.split(".")[0])
                    elif isinstance(inner, (ast.ClassDef, ast.FunctionDef)):
                        names.add(inner.name)
                    elif isinstance(inner, ast.Assign):
                        for target in inner.targets:
                            names |= bound_by(target)
    return names


def resolve(node: ast.ImportFrom, here: Path) -> Path | None:
    """Куда ведёт `from ... import`. None — не наш модуль, не проверяем."""
    if node.level:
        base = here.parent
        for _ in range(node.level - 1):
            base = base.parent
        parts = (node.module or "").split(".") if node.module else []
    else:
        parts = (node.module or "").split(".")
        if not parts or parts[0] not in OURS:
            return None
        base = ROOT

    target = base.joinpath(*parts) if parts else base
    if target.is_dir():
        return target / "__init__.py"
    module = target.with_suffix(".py")
    return module if module.exists() else None


def submodules(package: Path) -> set[str]:
    """Что можно импортировать из пакета как подмодуль."""
    folder = package.parent
    if not folder.is_dir():
        return set()
    out = {p.stem for p in folder.glob("*.py") if p.stem != "__init__"}
    out |= {p.name for p in folder.iterdir() if (p / "__init__.py").exists()}
    return out


def main() -> int:
    problems: list[str] = []
    checked = 0

    for path in sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(ROOT)}: не разбирается — {exc}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = resolve(node, path)
            if target is None or not target.exists():
                continue
            available = defined_names(target)
            if target.name == "__init__.py":
                available |= submodules(target)
            else:
                # `from .drivers import ld2410` внутри пакета — это подмодуль
                # рядом, а не имя внутри файла.
                available |= {p.stem for p in target.parent.glob("*.py")}
            for alias in node.names:
                checked += 1
                if alias.name == "*" or alias.name in available:
                    continue
                problems.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f"импортирует {alias.name!r}, а в "
                    f"{target.relative_to(ROOT)} такого имени нет")

    print(f"\n  файлов {len(sources())}, импортируемых имён {checked}")
    if not problems:
        print("  все импорты ведут к существующим именам\n")
        return 0
    print(f"\n  ПРОБЛЕМ: {len(problems)}\n")
    for problem in problems:
        print(f"    {problem}")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
