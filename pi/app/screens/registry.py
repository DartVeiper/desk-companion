"""Реестр экранов: состав и порядок берутся из конфига, а не зашиты в код.

Это пункт «система виджетов как модулей» из бэклога п.12 — включить или
выключить режим можно правкой config.toml, не трогая код.

Реестр намеренно глупый: он только держит список и указатель. Всю
маршрутизацию ввода делает Director — иначе логика покоя и подробностей
расползлась бы по двум местам.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

from .base import Screen


def instantiate(entry: str) -> Screen:
    """«clock.ClockScreen» -> объект экрана."""
    module_name, _, class_name = entry.rpartition(".")
    module = importlib.import_module(f"app.screens.{module_name}")
    return getattr(module, class_name)()


class ScreenRegistry:
    def __init__(self, screens: list[Screen], manual: str | None = None) -> None:
        if not screens:
            raise ValueError("реестр пуст: в конфиге не включён ни один экран")
        self._screens = screens
        self._index = 0
        self.manual = manual

    @property
    def current(self) -> Screen:
        return self._screens[self._index]

    @property
    def index(self) -> int:
        return self._index

    @property
    def screens(self) -> list[Screen]:
        return list(self._screens)

    def next(self) -> None:
        self._index = (self._index + 1) % len(self._screens)

    def prev(self) -> None:
        self._index = (self._index - 1) % len(self._screens)

    def home(self) -> None:
        self._index = 0

    def go_to(self, name: str) -> bool:
        for i, screen in enumerate(self._screens):
            if screen.name == name:
                self._index = i
                return True
        return False


def load_config(path: Path | str) -> dict:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def load(config_path: Path | str) -> ScreenRegistry:
    cfg = load_config(config_path)["screens"]
    return ScreenRegistry([instantiate(e) for e in cfg["enabled"]], cfg.get("manual"))
