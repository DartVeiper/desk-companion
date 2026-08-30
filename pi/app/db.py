"""Доступ к SQLite. Схема — app/schema.sql, п.4 плана.

Главное правило оттуда: пишем агрегаты, а не события. Поминутная строка
активности вместо строки на нажатие — иначе база пухнет и microSD изнашивается
(п.6). При записи раз в минуту это не проблема на годы вперёд.

Записи идут через INSERT OR REPLACE по времени как ключу: если минута или
пятиминутка приехала дважды (перезапуск сервиса, догон после обрыва), она
перезапишется, а не задвоится.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"

#: На Pi база лежит там, куда её кладёт setup-step1.sh. Вне Pi — рядом с кодом,
#: чтобы разработка не требовала прав на /var.
PI_PATH = Path("/var/lib/desk-companion/desk.db")
DEV_PATH = HERE.parent / "data" / "desk.db"

TS = "%Y-%m-%d %H:%M:%S"


def default_path() -> Path:
    return PI_PATH if PI_PATH.parent.exists() else DEV_PATH


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- запись

    def add_state_event(
        self,
        ts: datetime,
        presence: bool,
        active_app: str = "",
        category: str = "",
        audio_active: bool = False,
        manual_status: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO state_events (ts, presence, active_app, category,"
            " audio_active, manual_status) VALUES (?, ?, ?, ?, ?, ?)",
            (ts.strftime(TS), int(presence), active_app, category,
             int(audio_active), manual_status),
        )
        self.conn.commit()

    def add_activity_minute(
        self,
        ts: datetime,
        keystrokes: int,
        mouse_clicks: int,
        mouse_distance_px: int,
        at_desk: bool,
        category: str = "",
    ) -> None:
        # Секунды режем: ключ — именно минута, иначе INSERT OR REPLACE
        # перестанет ловить повторы.
        minute = ts.replace(second=0, microsecond=0)
        self.conn.execute(
            "INSERT OR REPLACE INTO activity_minute (ts, keystrokes, mouse_clicks,"
            " mouse_distance_px, at_desk, category) VALUES (?, ?, ?, ?, ?, ?)",
            (minute.strftime(TS), keystrokes, mouse_clicks, mouse_distance_px,
             int(at_desk), category),
        )
        self.conn.commit()

    def add_env(self, ts: datetime, co2: int, temperature: float, humidity: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO env_readings (ts, co2, temperature, humidity)"
            " VALUES (?, ?, ?, ?)",
            (ts.replace(second=0, microsecond=0).strftime(TS), co2, temperature, humidity),
        )
        self.conn.commit()

    def executemany_activity(self, rows: list[tuple]) -> None:
        """Пакетная вставка — для генератора и догона после обрыва."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO activity_minute (ts, keystrokes, mouse_clicks,"
            " mouse_distance_px, at_desk, category) VALUES (?, ?, ?, ?, ?, ?)", rows)
        self.conn.commit()

    def executemany_env(self, rows: list[tuple]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO env_readings (ts, co2, temperature, humidity)"
            " VALUES (?, ?, ?, ?)", rows)
        self.conn.commit()

    def executemany_events(self, rows: list[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO state_events (ts, presence, active_app, category,"
            " audio_active, manual_status) VALUES (?, ?, ?, ?, ?, ?)", rows)
        self.conn.commit()

    # ------------------------------------------------------------- чтение

    def activity_between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM activity_minute WHERE ts >= ? AND ts < ? ORDER BY ts",
            (start.strftime(TS), end.strftime(TS)),
        ).fetchall()

    def activity_for_day(self, day: date) -> list[sqlite3.Row]:
        start = datetime.combine(day, datetime.min.time())
        return self.activity_between(start, start + timedelta(days=1))

    def env_between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM env_readings WHERE ts >= ? AND ts < ? ORDER BY ts",
            (start.strftime(TS), end.strftime(TS)),
        ).fetchall()

    def latest_env(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM env_readings ORDER BY ts DESC LIMIT 1").fetchone()

    def recent_events(self, limit: int = 100, since: datetime | None = None) -> list[sqlite3.Row]:
        if since is None:
            return self.conn.execute(
                "SELECT * FROM state_events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return self.conn.execute(
            "SELECT * FROM state_events WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (since.strftime(TS), limit)).fetchall()

    def span(self) -> tuple[datetime | None, datetime | None]:
        """Первая и последняя минута в базе. Нужно дашборду, чтобы знать,
        есть ли вообще что показывать (п.10 плана: Режим 6 не включать,
        пока не накопится 1-2 недели)."""
        row = self.conn.execute(
            "SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM activity_minute").fetchone()
        if not row or row["lo"] is None:
            return None, None
        return datetime.strptime(row["lo"], TS), datetime.strptime(row["hi"], TS)

    def size_bytes(self) -> int:
        """Размер вместе с журналом WAL: свежие записи какое-то время лежат
        именно там, и без него база выглядит подозрительно пустой."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = self.path.with_name(self.path.name + suffix)
            if candidate.exists():
                total += candidate.stat().st_size
        return total

    def checkpoint(self) -> None:
        """Слить журнал в основной файл. Перед бэкапом обязательно, иначе
        копия окажется без последних записей."""
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
