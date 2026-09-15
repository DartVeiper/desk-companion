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
    #: Все экраны, которые человек когда-либо видел в списке настроек.
    #: Нужен, чтобы отличить «выключил» от «появился с обновлением»: в
    #: screens.enabled лежат только включённые, и по их отсутствию эти два
    #: случая неразличимы.
    "screens.known": list,
    "ambient.enabled": list,
    "ambient.away_delay_minutes": int,
    "ambient.night_from": int,
    "ambient.night_to": int,
    "ambient.night_style": str,
    "display.brightness": int,
    "air.co2_warn": int,
    "air.co2_alert": int,
    #: Сила нажатия, с которой панель считает касание состоявшимся. Ручка
    #: здесь, а не только в калибровке, потому что правильного значения нет:
    #: оно зависит от того, ногтем человек тыкает или подушечкой пальца, и
    #: подбирается на ощупь, а не замером.
    "touch.max_resistance": int,
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
    """Сохранить только разрешённое и только верных типов."""
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

    _write(clean, path)
    return clean


def forget(keys: list[str], path: Path | None = None) -> dict:
    """Убрать ручные значения, вернув ключи под власть конфига.

    Нужно калибровкам. Они пишут замеры в calibration.toml, который лежит
    слоем ниже этого файла, — и ручка, однажды сдвинутая человеком, молча
    отменяла бы любую последующую калибровку. Человек при этом видел бы,
    как калибровка печатает новое значение, и как оно не действует.

    Правило простое: побеждает сделанное последним. Подвинул ползунок —
    работает ползунок; откалибровал — ползунок забыт.

    Читаем сырой файл, а не load(): тот подмешивает умолчания, и забывание
    одного ключа записало бы в файл все остальные.
    """
    path = path or DEFAULT_PATH
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    for key in keys:
        stored.pop(key, None)
    _write(stored, path)
    return load(path)


def _write(values: dict, path: Path) -> None:
    """Записать словарь целиком — через временный файл и переименование.

    Иначе сервис однажды прочитает наполовину записанный JSON: он
    перечитывает файл по времени правки, а правка не мгновенна.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(values, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(config: dict, overrides: dict | None = None) -> dict:
    """Наложить настройки на конфиг. Исходный словарь не трогаем."""
    merged = copy.deepcopy(config)
    saved = overrides if overrides is not None else load()

    for key, value in saved.items():
        section, _, name = key.partition(".")
        merged.setdefault(section, {})[name] = value

    if "screens.enabled" in saved:
        merged["screens"]["enabled"] = with_new_screens(
            config.get("screens", {}).get("enabled", []),
            saved["screens.enabled"],
            saved.get("screens.known"),
        )
    return merged


def with_new_screens(from_config: list, chosen: list,
                     known: list | None = None) -> list:
    """Сохранённый порядок плюс экраны, появившиеся с обновлением.

    Без этого любой новый экран оказывался невидимым для всех, кто хоть раз
    открывал настройки: сохранённый список заменял список из конфига
    целиком, и добавленное обновлением в него просто не попадало.

    Отличить «человек выключил» от «появилось в обновлении» можно только по
    списку виденного. Если его ещё нет — файл настроек старый, — считаем
    виденным то, что включено: для того, кто ничего не выключал, это то же
    самое, а после первого же сохранения список станет точным.
    """
    seen = set(known if known is not None else chosen)
    fresh = [name for name in from_config if name not in seen and name not in chosen]
    return list(chosen) + fresh


def mtime(path: Path | None = None) -> float:
    """Время правки — по нему сервис понимает, что пора перечитать."""
    path = path or DEFAULT_PATH
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
