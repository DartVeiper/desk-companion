"""Снимок живого состояния для дашборда.

Сервис и дашборд — разные процессы. База их не связывает: туда идут
агрегаты раз в минуту, а панели здоровья и калибровки радара нужно «прямо
сейчас», причём радарные данные в базу вообще не попадают.

Механизм — файл-снимок в JSON. Не сокет и не брокер, потому что: работает
без mosquitto (то есть до шага 1), переживает перезапуск любой из сторон,
и читается чем угодно, вплоть до cat.

Про износ карты. Писать раз в секунду на microSD было бы ровно тем, от чего
предостерегает п.6 плана. Поэтому по умолчанию пишем в /run — это tmpfs,
то есть оперативная память, и карта не трогается вовсе. Потеря снимка при
перезагрузке ничего не значит: он и описывает только текущий момент.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .state import State

RUNTIME_DIR = Path("/run/desk-companion")
FALLBACK_DIR = Path(__file__).resolve().parents[1] / "preview"
FILENAME = "status.json"

#: Снимок старше этого — сервис не отвечает.
STALE_SECONDS = 15


def default_path() -> Path:
    """/run на Pi, каталог превью на машине разработки."""
    if RUNTIME_DIR.parent.exists():
        return RUNTIME_DIR / FILENAME
    return FALLBACK_DIR / FILENAME


def snapshot(state: State, sources: list | None = None,
             loop: dict | None = None) -> dict:
    """Собрать снимок. Радар отдаёт энергию по воротам, если он в строю."""
    health = state.health
    data = {
        "ts": state.now.isoformat(timespec="seconds"),
        "presence": state.desk.presence,
        "manual_status": state.desk.manual_status,
        "streak_days": state.desk.streak_days,
        "env": {"co2": state.env.co2, "temperature": state.env.temperature,
                "humidity": state.env.humidity},
        "weather": {"temp": state.weather.temp, "cond": state.weather.cond,
                    "rain_soon_minutes": state.weather.rain_soon_minutes},
        "pc_online": state.pc_online,
        # Данные игрового ПК: их показывает дашборд, и по ним же видно,
        # доехал ли агент, — без этого «онлайн» говорит лишь о сердцебиении.
        "pc": {
            "active_app": state.pc.active_app, "category": state.pc.category,
            "keystrokes": state.pc.keystrokes, "mouse_clicks": state.pc.mouse_clicks,
            "audio_active": state.pc.audio_active, "afk": state.pc.afk,
            "gpu_temp": state.pc.gpu_temp, "gpu_load": state.pc.gpu_load,
            "cpu_temp": state.pc.cpu_temp, "cpu_load": state.pc.cpu_load,
            "track": state.now_playing,
        },
        "health": {
            "wifi_ok": health.wifi_ok, "wifi_ssid": health.wifi_ssid,
            "ip": health.ip, "mqtt_ok": health.mqtt_ok,
            "scd41_ok": health.scd41_ok, "ld2410_ok": health.ld2410_ok,
            "throttled": health.throttled, "disk_free_pct": round(health.disk_free_pct, 1),
            "cpu_temp": health.cpu_temp, "uptime_seconds": health.uptime_seconds,
            "wifi_signal_dbm": health.wifi_signal_dbm,
            "ram_used_mb": health.ram_used_mb, "ram_total_mb": health.ram_total_mb,
            "version": health.version, "input_rejected": health.input_rejected,
        },
        "problems": [{"label": label, "detail": detail, "critical": critical}
                     for label, detail, critical in state.problems()],
        "sources": [],
        "radar": None,
        # Как себя чувствует сам цикл. Без этих трёх чисел вопрос
        # «почему процессор занят» решается гаданием: проходов много,
        # кадров много или один проход стал дорогим — снаружи не видно,
        # а ответы требуют разного лечения.
        "loop": loop or {},
    }

    for source in sources or []:
        data["sources"].append({
            "name": source.name, "ok": source.ok,
            "error": source.last_error, "failures": source.failures,
        })
        report = getattr(source, "last_report", None)
        if report is not None:
            data["radar"] = {
                "state": report.state_name,
                "present": report.present,
                "distance_cm": report.distance_cm,
                "moving_energy": report.moving_energy,
                "static_energy": report.static_energy,
                # Ради этих двух массивов всё и затевалось: без энергии по
                # зонам подбор порогов радара — гадание вслепую (п.9).
                "moving_gates": report.moving_gates,
                "static_gates": report.static_gates,
            }
    return data


def write(data: dict, path: Path | None = None) -> Path:
    """Записать снимок целиком, без промежуточных состояний.

    Через временный файл и переименование: иначе дашборд рано или поздно
    прочитает наполовину записанный JSON и упадёт на разборе.
    """
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def read(path: Path | None = None) -> dict | None:
    """Прочитать снимок. None — сервис не запущен или файл битый."""
    path = path or default_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    age = (datetime.now() - datetime.fromisoformat(data["ts"])).total_seconds()
    data["age_seconds"] = round(age, 1)
    data["stale"] = age > STALE_SECONDS
    return data
