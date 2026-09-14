"""Источники, живущие на самом Pi: таймеры и производные величины.

Здесь нет ни сети, ни железа — только логика, которую всё равно надо
крутить каждый тик.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ..state import State
from ..db import Database
from ..stats import streak_days
from .base import Source


class ManualResetSource(Source):
    """Автосброс ручного статуса — шаг 8 плана.

    П.6 предупреждает прямо: без таймера возврата легко забыть, что «не
    беспокоить» стоит с прошлой недели. Срок ставит сам экран Режима 4 при
    подтверждении, здесь только проверка, что он вышел.

    Отдельным источником, а не веткой в главном цикле: цикл должен
    оставаться пустым конвейером, иначе в него постепенно наползёт вся
    предметная логика.
    """

    name = "manual-reset"
    interval = 1.0

    def poll(self, state: State) -> bool:
        desk = state.desk
        if desk.manual_until is None or state.now < desk.manual_until:
            return False
        desk.manual_status = None
        desk.manual_until = None
        return True

    # Совместимость со старым протоколом update(state), на который опирается
    # test_director.py.
    def update(self, state: State) -> None:
        self.poll(state)


class AnomalySource(Source):
    """Режим 6: оценка текущего двухчасового окна обученной моделью.

    Раз в четверть часа: окно всё равно двухчасовое, чаще пересчитывать
    нечего, а на Zero 2 W лишняя выборка из базы не бесплатна.

    Без файла модели источник молчит и Режим 6 показывает «всё как обычно».
    Так и задумано: п.10 требует сначала накопить 1-2 недели наблюдений,
    и до тех пор индикатор обязан не срабатывать, а не выдумывать.
    """

    name = "anomaly"
    interval = 900.0

    def __init__(self, db: Database, model_path: Path | None = None) -> None:
        super().__init__()
        self.db = db
        self.model = None
        path = model_path or Path(__file__).resolve().parents[1] / "anomaly.pkl"
        if path.exists():
            from ..anomaly import AnomalyModel

            candidate = AnomalyModel()
            if candidate.load(path):
                self.model = candidate

    def poll(self, state: State) -> bool:
        if self.model is None:
            return False

        from ..anomaly import windows_for

        today = state.now.date()
        windows = windows_for(self.db, today, today)
        if not windows:
            return False

        flagged, reason = self.model.score(windows[-1])
        if flagged == state.pc.anomaly_flag and reason == state.pc.anomaly_reason:
            return False
        state.pc.anomaly_flag = flagged
        state.pc.anomaly_reason = reason
        return True


class EnvTrendSource(Source):
    """Ход CO2 за последние часы — для спарклайна на экране воздуха.

    Берём из базы, а не копим в памяти: в памяти история начиналась бы с
    последнего перезапуска сервиса, а перезапускается он при каждой
    доставке кода. График, который обнуляется от постороннего действия,
    обманывает.

    Раз в пять минут: сама база пополняется раз в десять, чаще спрашивать
    нечего.
    """

    name = "env-trend"
    interval = 300.0

    #: За сколько часов показываем ход. Шесть — это «с обеда до вечера»:
    #: достаточно, чтобы увидеть, как комната надышалась, и достаточно
    #: мало, чтобы утренний провал не сплющил вечерний подъём.
    HOURS = 6
    #: Сколько точек рисуем. Больше не нужно: полоса шириной в пару сотен
    #: точек всё равно не покажет разницы.
    POINTS = 48

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db

    def poll(self, state: State) -> bool:
        end = state.now
        rows = self.db.env_between(end - timedelta(hours=self.HOURS), end)
        values = [int(row["co2"]) for row in rows if row["co2"]]
        if len(values) > self.POINTS:
            # Прореживаем равномерно, а не берём последние: нужен весь
            # промежуток, иначе график перестанет быть про шесть часов.
            step = len(values) / self.POINTS
            values = [values[int(i * step)] for i in range(self.POINTS)]
        if values == state.env.trend:
            return False
        state.env.trend = values
        return True


class StreakSource(Source):
    """Стрик привычек для Режима 7.

    Считается поверх activity_minute на самом Pi (п.3), а не приезжает от
    ПК-агента. Раз в час: чаще незачем, а запрос перебирает дни назад.
    """

    name = "streak"
    interval = 3600.0

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db

    def poll(self, state: State) -> bool:
        days = streak_days(self.db, date.today())
        if days == state.desk.streak_days:
            return False
        state.desk.streak_days = days
        return True
