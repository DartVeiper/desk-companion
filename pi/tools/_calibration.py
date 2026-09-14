"""Запись результатов калибровки в отдельный файл.

Почему отдельный. `config.toml` — часть проекта: он лежит в репозитории,
едет на плату с кодом и одинаков у всех. Замеры же принадлежат конкретной
панели и конкретной комнате: пороги тача зависят от того, как собрана
плёнка, пороги радара — от того, где стоят стены.

Но главная причина не в чистоте, а в потере. Пока калибровка писалась в
config.toml, она жила до следующей доставки кода: tar перезаписывал файл
тем, что лежит в репозитории, и подобранные по суточной статистике пороги
радара стирались молча. Заметить это можно было только по поведению блока
через несколько дней.

Файл читается поверх config.toml (см. `app/screens/registry.load_config`),
исключён из доставки и не попадает в репозиторий.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

HEADER = """# Замеры этого экземпляра. Файл создаётся калибровкой и накладывается
# поверх config.toml.
#
# Править руками можно, но осторожно: следующая калибровка перезапишет
# свой раздел целиком. Постоянные настройки — в config.toml.
"""


def _format(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format(v) for v in value) + "]"
    return str(value)


def save(path: Path, section: str, values: dict,
         notes: dict[str, str] | None = None) -> None:
    """Записать раздел, сохранив остальные.

    notes — комментарии к отдельным ключам: файл читают руками, и число
    без объяснения через месяц выглядит случайным.
    """
    existing: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            existing = tomllib.load(fh)
    existing.setdefault(section, {})
    existing[section].update(values)

    lines = [HEADER]
    for name, block in existing.items():
        lines.append(f"[{name}]")
        for key, value in block.items():
            note = (notes or {}).get(key) if name == section else None
            if note:
                for row in note.split("\n"):
                    lines.append(f"# {row}")
            lines.append(f"{key} = {_format(value)}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
