"""Снимки приложения для документации.

    py pc/shots.py          # docs/приложение.png, по-русски
    py pc/shots.py --en     # docs/app.png, по-английски

Снимает страницы окна и склеивает их в одну картинку.

Зачем скриптом. В README сказано, что все картинки в docs/ собираются
кодом, и это не поза: картинка, собранная руками, устаревает молча.
Именно так и вышло — на прежнем снимке в списке было восемь экранов,
хотя на устройстве их девять уже неделю.

Приложение умеет рисовать себя в файл ключом --shot: окно создаётся,
отрисовывается и закрывается, не показываясь человеку. Данные при этом
настоящие — берутся с блока, если он в сети.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "Программа" / "DeskCompanion.exe"
ENGLISH = "--en" in sys.argv
LANGUAGE = "en" if ENGLISH else "ru"
OUT = ROOT / "docs" / ("app.png" if ENGLISH else "приложение.png")

#: Какие страницы показать и в каком порядке. Номера — те же, что у кнопок
#: слева: 0 обзор, 1 экраны, 2 статистика, 3 радар, 4 компьютер,
#: 5 настройки. Радар — потому что это самое наглядное, что умеет окно
#: сверх данных, которые и так видно на блоке.
PAGES = ("0", "3")

GAP = 24        # промежуток между панелями
MARGIN = 16     # поля вокруг всего


def shot(page: str, folder: Path) -> Image.Image | None:
    target = folder / f"page{page}.png"
    subprocess.run([str(EXE), "--shot", str(target), "--page", page,
                    "--lang", LANGUAGE], check=False)

    # Окно рисует себя и выходит само, но выход асинхронный: без ожидания
    # файл открывается наполовину записанным.
    for _ in range(40):
        if target.exists() and target.stat().st_size > 0:
            time.sleep(0.4)
            break
        time.sleep(0.25)
    else:
        return None
    return Image.open(target).convert("RGB")


def main() -> int:
    if not EXE.exists():
        print(f"\n  не найдено: {EXE}")
        print("  сначала собери:  powershell -ExecutionPolicy Bypass -File pc\\build.ps1\n")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        panels = [shot(page, Path(tmp)) for page in PAGES]
        if any(panel is None for panel in panels):
            print("\n  приложение не отдало снимок — окно не собралось\n")
            return 1

        width = sum(p.width for p in panels) + GAP * (len(panels) - 1) + MARGIN * 2
        height = max(p.height for p in panels) + MARGIN * 2
        # Фон тот же, что у окна: светлая рамка вокруг тёмного интерфейса
        # выглядит как скриншот, сделанный второпях.
        sheet = Image.new("RGB", (width, height), (8, 10, 14))

        x = MARGIN
        for panel in panels:
            sheet.paste(panel, (x, MARGIN))
            x += panel.width + GAP

        OUT.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(OUT)

    print(f"\n  собрано: {OUT.relative_to(ROOT)}  {sheet.width}x{sheet.height}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
