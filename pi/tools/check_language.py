"""Что осталось непереведённым на английском языке.

    python3 tools/check_language.py

Как это работает. Перевод живёт не в коде экранов, а в обёртке над
`draw.text` — см. app/lang.py. Значит, и проверять его надо не чтением
кода, а прогоном: рисуем каждый экран на английском, на всех состояниях
данных, и смотрим, какие русские строки прошли мимо словаря.

Это сильнее, чем «все ли вызовы обёрнуты». Обёрнутый вызов с
отсутствующим переводом выглядит правильным в коде и русским на экране;
здесь он виден.

Строки, собранные из переменных, словарь поймать не может: «6 ч без
перерыва» в нём не лежит. Такие места надо собирать иначе —
`t("{h} ч без перерыва").format(h=...)`, — и они тоже всплывут здесь.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app import lang  # noqa: E402
from app import theme  # noqa: E402
from app.screens import registry as registry_mod  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config.toml"


def screens() -> list:
    """Все экраны каталога, а не только включённые.

    Выключенный экран тоже однажды включат, и переведён он должен быть
    заранее — иначе перевод «готов» ровно до первой правки настроек.
    """
    from importlib import import_module

    config = registry_mod.load_config(CONFIG)
    names = list(config["screens"]["enabled"])
    for key in ("manual", "settings"):
        entry = config["screens"].get(key)
        if entry:
            names.append(entry)
    names += list(config.get("ambient", {}).get("enabled", []))

    made = []
    for entry in dict.fromkeys(names):
        try:
            made.append(registry_mod.instantiate(entry))
        except Exception as error:  # noqa: BLE001
            print(f"  не собрался экран {entry}: {error}")
    # Экраны подробностей в списке не значатся — они висят на карточках.
    for screen in list(made):
        made.extend(getattr(screen, "details", []))
    return made


def content(state) -> set[str]:
    """Что на экране — не подпись, а данные.

    Название трека, заголовок окна, имя сети Wi-Fi, город. Переводить их
    нельзя ни при каком языке: это чужой текст, а не наш интерфейс. Без
    этого списка проверка вечно ругалась бы на них, и среди её ругани
    терялось бы настоящее.

    Собираем из самого состояния, а не списком в этом файле: список
    пришлось бы вести руками и он разошёлся бы с данными.
    """
    out = set()
    for owner, field in (("pc", "active_app"), ("pc", "anomaly_reason"),
                         ("health", "wifi_ssid"), ("weather", "place"),
                         ("", "now_playing")):
        holder = getattr(state, owner, state) if owner else state
        value = getattr(holder, field, None)
        if isinstance(value, str) and value.strip():
            out.add(value)
    return out


def main() -> int:
    # Состояния берём те же, на которых проверяется вёрстка: пусто, полно,
    # аварийно. Две реализации «какие бывают данные» разошлись бы.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_layout import states  # noqa: E402

    lang.use("en")
    lang.missed.clear()

    data: set[str] = set()
    drawn = 0
    for screen in screens():
        for _, state in states():
            data |= content(state)
            frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
            draw = lang.wrap(ImageDraw.Draw(frame))
            try:
                screen.render(state, draw, frame)
            except Exception as error:  # noqa: BLE001
                print(f"  {screen.name} упал на отрисовке: {error}")
                continue
            drawn += 1

    # Обрезанный трек («Оркестр имени…») — тоже данные, поэтому сверяем по
    # вхождению начала, а не по равенству.
    def is_content(text: str) -> bool:
        stripped = text.rstrip("…").strip()
        return any(stripped and (stripped in value or value in text)
                   for value in data)

    left = sorted(t for t in lang.missed if not is_content(t))

    print(f"\n  отрисовок на английском: {drawn}, "
          f"строк в словаре: {len(lang.EN)}, "
          f"пропущено как данные: {len(lang.missed) - len(left)}")

    if not left:
        print("  весь текст экранов переведён\n")
        return 0

    print(f"\n  НЕ ПЕРЕВЕДЕНО ({len(left)}):\n")
    for text in left:
        print(f"    {text!r}")
    print("\n    Строки без переменных — добавить в EN в app/lang.py.")
    print("    Строки с подставленными значениями собираются в коде экрана;")
    print("    их надо переписать на t(\"...{}...\").format(...).\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
