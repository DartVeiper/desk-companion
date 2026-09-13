"""Проверка, не держит ли железо работающий сервис.

Инструменты калибровки и проверки берут те же ножки и ту же шину, что и
`desk-companion`. Если сервис работает, gpiozero падает с сообщением про
занятый GPIO — по нему невозможно догадаться, что виноват не провод, а
собственная служба. Особенно обидно при первом подключении узла, когда
поломку и так ищут в пайке.
"""

from __future__ import annotations

import subprocess

UNIT = "desk-companion"


def running() -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", UNIT],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False  # не systemd или нет systemctl — значит и сервиса нет
    return out.strip() == "active"


def require_stopped() -> None:
    """Остановиться самому, если железо занято сервисом."""
    if not running():
        return
    print(f"\n  Сервис {UNIT} сейчас работает и держит экран, шину и ножки.")
    print("  Останови его на время проверки, потом верни обратно:\n")
    print(f"      sudo systemctl stop {UNIT}")
    print("      <эта команда>")
    print(f"      sudo systemctl start {UNIT}\n")
    raise SystemExit(2)
