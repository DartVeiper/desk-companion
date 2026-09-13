"""Единая структура состояния — шаг 7 плана.

Источники (датчики, MQTT, ввод) пишут сюда, экраны только читают. Состояние
не размазано по экранам: любой экран может показать любое поле, а новый
источник данных не трогает отрисовку.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Три пропуска подряд при периоде 30 с. Без heartbeat Pi молча считает,
# что на ПК всё по-старому (п.5 плана).
HEARTBEAT_TIMEOUT = timedelta(seconds=90)


@dataclass
class Env:
    """SCD41 по I2C."""

    co2: int | None = None
    temperature: float | None = None
    humidity: float | None = None
    updated: datetime | None = None


@dataclass
class Weather:
    """Погодный API и nowcast."""

    temp: float | None = None
    cond: str = ""
    rain_soon_minutes: int | None = None  # None — дождя не ожидается


@dataclass
class Desk:
    """Что Pi знает сам: радар, энкодер, расчёты поверх базы."""

    presence: bool = False
    presence_since: datetime | None = None
    manual_status: str | None = None
    manual_until: datetime | None = None
    streak_days: int = 0


@dataclass
class Pc:
    """Что приезжает от ПК-агента по MQTT."""

    active_app: str = ""
    category: str = ""  # code / game / browser / other
    keystrokes: int = 0
    mouse_clicks: int = 0
    audio_active: bool = False
    afk: bool = False
    gpu_temp: float | None = None
    gpu_load: float | None = None
    cpu_temp: float | None = None
    cpu_load: float | None = None
    anomaly_flag: bool = False
    anomaly_reason: str = ""
    last_heartbeat: datetime | None = None
    #: Что звучит на ПК. Пустое название — не играет ничего.
    #: В базу это не пишется намеренно: минутные агрегаты и так дают
    #: картину дня, а строка на каждую песню утопила бы их шумом.
    track_artist: str = ""
    track_title: str = ""
    track_playing: bool = False

    @property
    def track(self) -> str:
        """Одной строкой: «исполнитель — название»."""
        if not self.track_playing or not self.track_title:
            return ""
        if not self.track_artist:
            return self.track_title
        return f"{self.track_artist} — {self.track_title}"


@dataclass
class Health:
    """Состояние самого блока. Нужно строке состояния и диагностике."""

    wifi_ok: bool = True
    wifi_ssid: str = ""
    wifi_signal_dbm: int | None = None
    ip: str = ""
    mqtt_ok: bool = True
    scd41_ok: bool = True
    ld2410_ok: bool = True
    #: vcgencmd get_throttled отличен от нуля — просадки питания. Ловит
    #: дохлый блок или кабель раньше, чем начнутся необъяснимые перезагрузки.
    throttled: bool = False
    disk_free_pct: float = 100.0
    cpu_temp: float | None = None  # температура самого Pi, не игрового ПК
    uptime_seconds: int = 0
    #: Память. На 512 МБ это первое, во что упирается блок, а заметить
    #: нехватку постфактум по перезапускам сервиса почти невозможно.
    ram_used_mb: int = 0
    ram_total_mb: int = 0
    #: Что за код сейчас работает. После каждой доставки первый вопрос —
    #: доехала ли она; без версии на это отвечают перезагрузкой наугад.
    version: str = ""
    #: Отброшенных переходов энкодера. Растёт при дребезге и пропущенных
    #: фронтах: если щелчки теряются, число видно здесь, а не на ощупь.
    input_rejected: int = 0


@dataclass
class State:
    now: datetime = field(default_factory=datetime.now)
    env: Env = field(default_factory=Env)
    weather: Weather = field(default_factory=Weather)
    desk: Desk = field(default_factory=Desk)
    pc: Pc = field(default_factory=Pc)
    health: Health = field(default_factory=Health)
    brightness: int = 80  # процент, PWM подсветки на GPIO18

    @property
    def pc_online(self) -> bool:
        hb = self.pc.last_heartbeat
        return hb is not None and (self.now - hb) < HEARTBEAT_TIMEOUT

    @property
    def now_playing(self) -> str:
        """Что звучит — но только пока ПК на связи.

        Проверка обязательна из-за того, как устроен MQTT: агент шлёт трек
        с признаком «сохранять», чтобы после перезапуска блока музыка
        появилась сразу, не дожидаясь следующей песни. Обратная сторона в
        том, что это же сообщение переживает и смерть агента: выключенный
        ПК оставил бы на часах песню, которая доиграла вчера.
        """
        return self.pc.track if self.pc_online else ""

    def problems(self) -> list[tuple[str, str, bool]]:
        """Неисправности блока: (метка, подробности, критично). Важное первым.

        Порядок неслучаен: питание рушит всё остальное, поэтому идёт первым.

        Выключенный игровой ПК сюда сознательно НЕ попадает. Ночью он выключен
        всегда, и постоянно горящий значок «ПК офлайн» приучил бы не смотреть
        на строку состояния вообще. Это нормальное состояние мира, а не отказ
        блока; в диагностике оно всё равно видно.
        """
        h = self.health
        found: list[tuple[str, str, bool]] = []
        if h.throttled:
            found.append(("питание", "просадки напряжения — проверь блок и кабель", True))
        if not h.wifi_ok:
            found.append(("нет сети", "WiFi отвалился: нет времени по NTP и погоды", True))
        if not h.mqtt_ok:
            found.append(("нет брокера", "mosquitto не отвечает — ПК-агент не достучится", True))
        if not h.scd41_ok:
            found.append(("нет CO2",
                          "датчик воздуха замолчал — снять питание с платы "
                          "физически, перезагрузка не помогает", False))
        if not h.ld2410_ok:
            found.append(("нет радара", "LD2410 молчит по UART — присутствие не определяется", False))
        if h.disk_free_pct < 10:
            found.append(("мало места", f"на карте свободно {h.disk_free_pct:.0f}%", False))
        return found
