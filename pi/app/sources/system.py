"""Здоровье самого блока: то, что показывает строка состояния и диагностика.

На Pi читает настоящие показатели, на Windows часть из них недоступна и
молча остаётся пустой — сервис должен запускаться и на машине разработки,
иначе отлаживать его негде.

Просадки питания ловим через vcgencmd get_throttled: п.6 плана требует
питать блок от настенного адаптера, а не от USB компьютера, и именно этот
флаг покажет, что требование нарушено.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

from ..state import State
from .base import Source


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cpu_temperature() -> float | None:
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    return round(int(raw) / 1000, 1) if raw and raw.isdigit() else None


def uptime_seconds() -> float:
    raw = _read("/proc/uptime")
    if raw:
        return float(raw.split()[0])
    return time.monotonic()  # на Windows — хотя бы аптайм процесса


def throttled() -> bool:
    """Ненулевой get_throttled: были просадки напряжения или перегрев."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return "=0x0" not in out


def memory_mb() -> tuple[int, int]:
    """Занято и всего, в мегабайтах. Из /proc/meminfo, без сторонних пакетов.

    Занятым считаем то же, что показывает free: всего минус доступное.
    Именно «доступное», а не «свободное» — кеш отдаётся под нужды программ
    и свободной памятью по сути является.
    """
    raw = _read("/proc/meminfo")
    if not raw:
        return 0, 0
    values = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
            values[parts[0].rstrip(":")] = int(parts[1])
    total = values.get("MemTotal", 0) // 1024
    available = values.get("MemAvailable", 0) // 1024
    return max(0, total - available), total


def code_version() -> str:
    """Версия доставленного кода: её кладёт deploy.sh рядом с приложением."""
    return (_read(str(Path(__file__).resolve().parents[2] / ".version")) or "")[:32]


def wifi_signal_dbm() -> int | None:
    """Уровень сигнала из /proc/net/wireless.

    Файлом, а не утилитой: iwgetid и iwconfig в свежей Raspberry Pi OS уже
    не ставятся, и вызов молча ничего не возвращал бы.
    """
    raw = _read("/proc/net/wireless")
    if not raw:
        return None
    for line in raw.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 4 or not parts[0].startswith("wlan"):
            continue
        try:
            return int(float(parts[3].rstrip(".")))
        except ValueError:
            return None
    return None


def wifi_ssid() -> str:
    """Имя сети через nmcli: в /proc его нет, а NetworkManager знает.

    Отсутствие nmcli не ошибка — на машине разработки его нет, и поле
    просто останется пустым.
    """
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if line.startswith("yes:"):
            return line.split(":", 1)[1].strip()
    return ""


def local_ip() -> str:
    """Адрес в локальной сети. UDP-сокет никуда не шлёт — только выясняет
    у ядра, через какой интерфейс пошёл бы трафик."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))  # адрес из TEST-NET-1, он гарантированно ничей
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


class SystemHealthSource(Source):
    name = "health"
    interval = 30.0

    def __init__(self, disk_path: str = "/") -> None:
        super().__init__()
        self.disk_path = disk_path if Path(disk_path).exists() else str(Path.home())

    def poll(self, state: State) -> bool:
        health = state.health
        usage = shutil.disk_usage(self.disk_path)

        health.cpu_temp = cpu_temperature()
        health.uptime_seconds = int(uptime_seconds())
        health.throttled = throttled()
        health.disk_free_pct = usage.free / usage.total * 100
        health.ip = local_ip()
        health.wifi_ssid = wifi_ssid()
        health.wifi_signal_dbm = wifi_signal_dbm()
        health.ram_used_mb, health.ram_total_mb = memory_mb()
        if not health.version:
            health.version = code_version()
        # Адрес есть — значит сеть работает, как бы ни звалась. Имя и
        # уровень нужны человеку, а не проверке.
        health.wifi_ok = bool(health.ip)
        return True
