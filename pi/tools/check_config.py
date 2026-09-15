"""Проверка, что настройки и код говорят об одном.

    python3 tools/check_config.py

Зачем. Самая частая поломка в этом проекте — не ошибка в логике, а
настройка, которая никуда не ведёт. Список уже длинный:

  engineering = true      лежал в конфиге, инженерный режим включал
                          отладочный скрипт, а сервис — нет
  max_resistance          порог тача из конфига не доходил до драйвера
  temperature_offset      `cfg.get(...) or None` превращал ноль в «не
                          трогать», и датчик жил на заводской поправке
  gate_moving/gate_static пороги радара драйвер умел собирать, но никто
                          не отправлял

Общее у них одно: и код, и конфиг выглядят правильными по отдельности.
Тесты проходят, ничего не падает, а настройка просто не делает ничего —
и понять это можно, только удивившись поведению через неделю.

Проверка смотрит с обеих сторон:

  1. Ключ есть в config.toml, но код его нигде не читает — мёртвая ручка.
  2. Код читает ключ, которого в config.toml нет — либо падение на
     KeyError, либо молчаливое значение по умолчанию вместо настройки.

Чего она не умеет. Она не знает, что код делает с прочитанным: ключ,
который читают и кладут в переменную, а дальше забывают, здесь выглядит
живым. Это ловится только чтением кода — зато всё остальное ловится тут.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "app" / "config.toml"

#: Имена словарей, обращение к которым считаем чтением настройки. Список
#: короткий намеренно: широкий даёт ложные срабатывания на любом словаре.
DICTS = {"cfg", "config", "block", "settings", "conf", "options"}

#: Ключи, которые читает не Python, а что-то ещё, — их отсутствие в коде
#: ни о чём не говорит.
ALLOWED_UNUSED: set[str] = set()


def sources() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def config_keys(data: dict, prefix: str = "") -> dict[str, str]:
    """Все ключи конфига: имя -> путь, по которому оно лежит."""
    found: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            # Имя раздела тоже объявлено: код берёт его как config["radar"],
            # и без этого проверка считала бы все разделы отсутствующими.
            found[key] = path
            found.update(config_keys(value, f"{path}."))
        else:
            found[key] = path
    return found


def written_by_calibration(tree: ast.AST) -> set[str]:
    """Ключи, которые калибровка кладёт в calibration.toml.

    Их нет и не должно быть в config.toml: это замеры конкретного
    экземпляра. Узнаём их прямо из вызовов _calibration.save, а не из
    списка в этом файле — список пришлось бы вести руками, и он разошёлся
    бы с кодом на первой же новой калибровке.
    """
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "save":
            continue
        target = node.func.value
        if not (isinstance(target, ast.Name) and target.id == "_calibration"):
            continue
        for argument in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(argument, ast.Dict):
                for key in argument.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
    return keys


def catalog_keys() -> set[str]:
    """Экраны, которые дашборд показывает в списке настроек.

    Читаем SCREEN_CATALOG разбором, а не импортом: dashboard_server тянет
    за собой базу и сервер, а проверка должна гоняться на голой машине.
    """
    source = ROOT / "dashboard_server.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "SCREEN_CATALOG" not in names:
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        keys = set()
        for item in node.value.elts:
            if isinstance(item, (ast.Tuple, ast.List)) and item.elts:
                first = item.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
        return keys
    return set()


def read_keys(tree: ast.AST) -> set[str]:
    """Ключи, которые модуль читает из словаря настроек."""
    keys: set[str] = set()
    for node in ast.walk(tree):
        # cfg["ключ"]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in DICTS and isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str):
                    keys.add(node.slice.value)
        # cfg.get("ключ") и cfg.get("ключ", запасное)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "get" or not node.args:
                continue
            target = node.func.value
            named = isinstance(target, ast.Name) and target.id in DICTS
            # config["radar"].get("ключ") — тоже чтение настройки
            chained = (isinstance(target, ast.Subscript)
                       and isinstance(target.value, ast.Name)
                       and target.value.id in DICTS)
            if (named or chained) and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    keys.add(node.args[0].value)
    return keys


def main() -> int:
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    declared = config_keys(data)

    used: set[str] = set()
    measured: set[str] = set()
    for path in sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        used |= read_keys(tree)
        measured |= written_by_calibration(tree)

    # Ключ считаем живым и тогда, когда он встречается в коде строкой:
    # его могут читать не через словарь, а, например, сравнением имени.
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in sources())

    dead = [name for name, path in sorted(declared.items(), key=lambda kv: kv[1])
            if name not in used
            and f'"{name}"' not in text and f"'{name}'" not in text
            and name not in ALLOWED_UNUSED]
    missing = sorted(key for key in used
                     if key not in declared and key not in measured)

    print(f"\n  ключей в config.toml: {len(declared)}, "
          f"читается кодом: {len(used)}, "
          f"из них замеров: {len(measured & used)}")

    problems = 0
    if dead:
        problems += len(dead)
        print(f"\n  МЁРТВЫЕ РУЧКИ — есть в конфиге, код не читает ({len(dead)}):\n")
        for name in dead:
            print(f"    {declared[name]}")
        print("\n    Либо подключить, либо убрать. Настройка, которая ничего")
        print("    не делает, хуже её отсутствия: на неё полагаются.")

    if missing:
        problems += len(missing)
        print(f"\n  ЧИТАЮТСЯ, НО В КОНФИГЕ ИХ НЕТ ({len(missing)}):\n")
        for key in missing:
            print(f"    {key}")
        print("\n    Через cfg[...] это падение при старте узла, через")
        print("    cfg.get(...) — молчаливое значение по умолчанию.")

    # Третья сверка: экраны из конфига против списка в настройках. Экран,
    # которого нет в каталоге дашборда, работает на устройстве, но не
    # показывается в настройках — его нельзя ни выключить, ни переставить,
    # и человек решает, что обновление его не привезло. Так случилось с
    # прогнозом: он листался на плате и отсутствовал в списке.
    catalog = catalog_keys()
    screens = set(data.get("screens", {}).get("enabled", []))
    if catalog:
        missing_in_catalog = sorted(screens - catalog)
        missing_in_config = sorted(catalog - screens)
        if missing_in_catalog:
            problems += len(missing_in_catalog)
            print(f"\n  ЭКРАНЫ, КОТОРЫХ НЕТ В СПИСКЕ НАСТРОЕК ({len(missing_in_catalog)}):\n")
            for key in missing_in_catalog:
                print(f"    {key}")
            print("\n    Листаются на устройстве, но в настройках их не видно:")
            print("    добавь в SCREEN_CATALOG в dashboard_server.py.")
        if missing_in_config:
            problems += len(missing_in_config)
            print(f"\n  ЭКРАНЫ ИЗ СПИСКА НАСТРОЕК, КОТОРЫХ НЕТ В КОНФИГЕ ({len(missing_in_config)}):\n")
            for key in missing_in_config:
                print(f"    {key}")
            print("\n    В настройках их предлагают включить, а включать нечего.")

    if not problems:
        print("  настройки и код сходятся\n")
    else:
        print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
