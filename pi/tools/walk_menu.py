"""Обход меню: прокликать все переходы и показать карту состояний.

    py tools/walk_menu.py            # карта переходов
    py tools/walk_menu.py --render   # плюс PNG каждого экрана

Проверять меню руками на устройстве дорого: экранов много, путей ещё
больше, а промах в одном переходе замечаешь через неделю. Здесь тот же
Director получает синтетические события и рассказывает, куда попал.

Ищем не «красиво ли», а три конкретные беды:
  ловушка   — из состояния нельзя выйти ни одним действием
  сирота    — до состояния нельзя добраться из дома
  сюрприз   — вращение уводит туда, куда человек не собирался
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import director as director_mod  # noqa: E402
from app.inputs.events import Action, InputEvent  # noqa: E402
from app.screens import registry as registry_mod  # noqa: E402
from app.state import State  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"
ACTIONS = [Action.NEXT, Action.PREV, Action.SELECT, Action.HOLD, Action.SETTINGS]


def fresh() -> tuple[director_mod.Director, State]:
    config = registry_mod.load_config(CONFIG)
    registry = registry_mod.ScreenRegistry(
        [registry_mod.instantiate(e) for e in config["screens"]["enabled"]])
    director = director_mod.from_config(config, registry)
    state = State(now=datetime(2026, 9, 13, 14, 0))
    state.desk.presence = True
    director.last_seen = state.now
    director.current(state)
    return director, state


def where(director: director_mod.Director, state: State) -> str:
    """Устойчивое имя состояния: режим карусели плюс стопка накладок.

    В имя входит и выбранный пункт меню, если экран его ведёт. Без этого
    обход считает, что вращение в меню «никуда не ведёт», и не доходит до
    пунктов ниже первого — то есть до всего интересного.
    """
    base = director.registry.current.name
    stack = director.overlay_names
    name = base + ("  >  " + " > ".join(stack) if stack else "")
    top = director.current(state)
    index = getattr(top, "_index", None)
    return name if index is None else f"{name} [{index}]"


def replay(path: list[Action]) -> tuple[director_mod.Director, State]:
    director, state = fresh()
    for action in path:
        director.handle(InputEvent(action, "walk", 0.0), state)
        director.current(state)
    return director, state


def main() -> None:
    render = "--render" in sys.argv

    seen: dict[str, list[Action]] = {}
    edges: dict[str, dict[str, str]] = {}
    queue: list[list[Action]] = [[]]
    director, state = fresh()
    seen[where(director, state)] = []

    while queue:
        path = queue.pop(0)
        for action in ACTIONS:
            director, state = replay(path)
            before = where(director, state)
            director.handle(InputEvent(action, "walk", 0.0), state)
            director.current(state)
            after = where(director, state)
            edges.setdefault(before, {})[action.name] = after
            if after not in seen and len(path) < 6:
                seen[after] = path + [action]
                queue.append(path + [action])

    print(f"\n  Состояний найдено: {len(seen)}\n")
    for name in sorted(seen):
        steps = seen[name]
        how = " → ".join(a.name for a in steps) if steps else "дом"
        print(f"  {name}")
        print(f"      путь: {how}")
        for action, target in edges.get(name, {}).items():
            mark = "  (никуда)" if target == name else ""
            print(f"      {action:9} -> {target}{mark}")
        print()

    print("  Разбор\n")
    problems = 0

    def screen_of(name: str) -> str:
        """Имя без выбранного пункта: перемещение курсора — не переход."""
        return name.split(" [")[0]

    def parent_of(name: str) -> str | None:
        """Та же стопка без верхней накладки. None — уже карусель."""
        if "  >  " not in name:
            return None
        base, _, stack = name.partition("  >  ")
        parts = stack.split(" > ")
        return base if len(parts) == 1 else base + "  >  " + " > ".join(parts[:-1])

    for name in sorted(seen):
        out = edges.get(name, {})
        if out and all(target == name for target in out.values()):
            print(f"  [ЛОВУШКА] {name}: ни одно действие не выводит наружу")
            problems += 1

    for name in sorted(seen):
        here = screen_of(name)
        for action in ("NEXT", "PREV"):
            there = screen_of(edges.get(name, {}).get(action, name))
            if there == here:
                continue  # курсор внутри экрана — так и надо
            if ">" not in here and ">" not in there:
                continue  # листаем карусель — так и надо
            if there == parent_of(here):
                # Вращение в накладке, которая его себе не забрала,
                # поднимает на уровень вверх. Раньше правило было
                # «не меняет экран вовсе», и эта проверка его закрепляла —
                # вместе с ловушкой: на экране подробностей молчали и
                # вращение, и свайп, и тап по краям, и выглядело это
                # зависшим блоком. Наверх — можно, вбок к соседней
                # накладке — по-прежнему сюрприз.
                continue
            print(f"  [СЮРПРИЗ] {name}: {action} уводит с {here} на {there}")
            problems += 1

    # Домой должно возвращать не больше чем за три действия: иначе человек,
    # заблудившись, начнёт дёргать питание.
    home = screen_of(sorted(n for n in seen if ">" not in n)[0])
    for name in sorted(seen):
        if ">" not in name:
            continue
        reach, frontier = {name}, [(name, 0)]
        ok = False
        while frontier:
            node, depth = frontier.pop(0)
            if ">" not in screen_of(node):
                ok = True
                break
            if depth >= 3:
                continue
            for target in edges.get(node, {}).values():
                if target not in reach:
                    reach.add(target)
                    frontier.append((target, depth + 1))
        if not ok:
            print(f"  [ДАЛЕКО] {name}: до карусели больше трёх действий")
            problems += 1

    reachable = {screen_of(n) for n in seen}
    print(f"\n  Экранов достижимо: {len(reachable)}")
    for name in sorted(reachable):
        print(f"      {name}")

    print(f"\n  Проблем: {problems}\n")

    if render:
        from PIL import Image, ImageDraw

        from app import theme
        out_dir = Path(__file__).resolve().parents[1] / "preview" / "меню"
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, steps in sorted(seen.items()):
            director, state = replay(steps)
            screen = director.current(state)
            frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
            screen.render(state, ImageDraw.Draw(frame), frame)
            safe = name.replace(" > ", "__").replace("  ", "").replace(" ", "")
            frame.save(out_dir / f"{safe}.png")
        print(f"  кадры: {out_dir}")

    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
