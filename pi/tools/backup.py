"""Бэкап базы — шаг 12 плана.

    py tools/backup.py --db /var/lib/desk-companion/desk.db --to /mnt/backup

П.6 предупреждает прямо: раз история живёт на одной microSD в одном
устройстве, при порче карты она пропадёт вся разом. А карта здесь расходник.

Копируем через SQLite backup API, а не файлом: при включённом WAL обычное
копирование может поймать базу в середине транзакции и дать битую копию,
которая при этом откроется без ошибок — и обнаружится это в худший момент.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

DEFAULT_KEEP = 14


def make_backup(db_path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"desk-{date.today().isoformat()}.db"

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(out)
        try:
            source.backup(destination)
            # Копия наследует WAL от оригинала и получается из трёх файлов.
            # Для архива это плохо: рядом накапливаются -wal и -shm, ротация
            # их не подбирает, а унести человек рискует только сам .db.
            # Переключаем на одиночный файл.
            destination.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination.close()
    finally:
        source.close()

    for leftover in (out.with_name(out.name + "-wal"), out.with_name(out.name + "-shm")):
        leftover.unlink(missing_ok=True)
    return out


def verify(path: Path) -> tuple[bool, str]:
    """Проверить копию, а не надеяться на неё.

    Бэкап, который никто не открывал, — это не бэкап.
    """
    try:
        # immutable=1 запрещает SQLite трогать файл вообще: иначе даже
        # чтение создаёт рядом -shm и копия снова перестаёт быть одним файлом.
        conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                return False, result
            rows = conn.execute("SELECT COUNT(*) FROM activity_minute").fetchone()[0]
            return True, f"{rows} строк активности"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, str(exc)


def rotate(target_dir: Path, keep: int) -> list[Path]:
    copies = sorted(target_dir.glob("desk-*.db"))
    doomed = copies[:-keep] if len(copies) > keep else []
    for path in doomed:
        path.unlink()
    return doomed


def main() -> None:
    parser = argparse.ArgumentParser(description="Резервная копия базы")
    parser.add_argument("--db", default="/var/lib/desk-companion/desk.db")
    parser.add_argument("--to", required=True,
                        help="куда класть: смонтированная шара, флешка, что угодно ВНЕ карты")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"  базы нет: {db_path}")
        raise SystemExit(1)

    target = Path(args.to)
    if target.resolve() == db_path.parent.resolve():
        print("  копия рядом с оригиналом смысла не имеет: карта умрёт вместе с обеими")
        raise SystemExit(1)

    out = make_backup(db_path, target)
    ok, detail = verify(out)
    size_kb = out.stat().st_size / 1024

    if not ok:
        out.unlink(missing_ok=True)
        print(f"  копия не прошла проверку и удалена: {detail}")
        raise SystemExit(1)

    removed = rotate(target, args.keep)
    print(f"  {out}  ({size_kb:.0f} КБ, {detail})")
    if removed:
        print(f"  удалено старых копий: {len(removed)}")


if __name__ == "__main__":
    main()
