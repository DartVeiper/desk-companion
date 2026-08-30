"""Настройки, меняемые из браузера.

Слой поверх config.toml, а не правка самого конфига. Причины две. Первая:
tomllib умеет только читать, а писать TOML руками — верный способ однажды
затереть комментарии, которых там больше, чем значений. Вторая: конфиг лежит
в репозитории и обновляется вместе с кодом, а настройки принадлежат
конкретному блоку и не должны конфликтовать при обновлении.

Формат — плоский JSON: «секция.ключ» -> значение. Плоский потому, что
слияние вложенных словарей порождает вопросы вроде «список заменяет или
дополняет», а здесь ответ всегда один — заменяет.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "settings.json"

#: Что вообще разрешено менять из браузера. Всё остальное игнорируется:
#: браузер не должен уметь переписать номера ножек.
ALLOWED = {
    "screens.enabled": list,
    "ambient.enabled": list,
    "ambient.away_delay_minutes": int,
    "ambient.night_from": int,
    "ambient.night_to": int,
    "ambient.night_style": str,
    "display.brightness": int,
    "air.co2_warn": int,
    "air.co2_alert": int,
}

#: Пороги CO2 живут здесь, а не в theme.py: их естественно крутить под свою
#: комнату, а тема — про оформление.
DEFAULTS = {"air.co2_warn": 800, "air.co2_alert": 1400, "display.brightness": 100}


def load(path: Path | None = None) -> dict:
    path = path or DEFAULT_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in ALLOWED}}


def save(values: dict, path: Path | None = None) -> dict:
    """Сохранить только разрешённое и только верных типов.

    Через временный файл и переименование: иначе сервис однажды прочитает
    наполовину записанный JSON.
    """
    path = path or DEFAULT_PATH
    clean = load(path)
    for key, value in values.items():
        expected = ALLOWED.get(key)
        if expected is None:
            continue
        try:
            clean[key] = [str(v) for v in value] if expected is list else expected(value)
        except (TypeError, ValueError):
            continue

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return clean


def apply(config: dict, overrides: dict | None = None) -> dict:
    """Наложить настройки на конфиг. Исходный словарь не трогаем."""
    merged = copy.deepcopy(config)
    for key, value in (overrides if overrides is not None else load()).items():
        section, _, name = key.partition(".")
        merged.setdefault(section, {})[name] = value
    return merged


def mtime(path: Path | None = None) -> float:
    """Время правки — по нему сервис понимает, что пора перечитать."""
    path = path or DEFAULT_PATH
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
