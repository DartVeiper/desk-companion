"""Датчик CO2/температуры/влажности SCD41 по I2C.

Транспорта нет — только команды, контрольные суммы и пересчёт сырых слов.
Значит проверяется тестами до того, как датчик припаян.

Отдельно про самокалибровку (ASC). По умолчанию датчик сам подстраивает
ноль, предполагая, что раз в несколько дней видит свежий уличный воздух
(около 400 ppm). Блок стоит в комнате круглосуточно, и если её редко
проветривают до уличного уровня, показания медленно уезжают. П.6 плана
требует ASC отключить — здесь для этого есть команда, а вызывать её надо
один раз с последующим persist_settings, иначе настройка не переживёт
отключение питания.
"""

from __future__ import annotations

from dataclasses import dataclass

ADDRESS = 0x62

# Команды. Задержки в миллисекундах — столько датчик думает перед ответом.
START_PERIODIC = 0x21B1
READ_MEASUREMENT = 0xEC05
STOP_PERIODIC = 0x3F86
GET_DATA_READY = 0xE4B8
SET_ASC = 0x2416
GET_ASC = 0x2313
SET_TEMPERATURE_OFFSET = 0x241D
PERSIST_SETTINGS = 0x3615
REINIT = 0x3646
GET_SERIAL = 0x3682
FACTORY_RESET = 0x3632

DELAYS_MS = {
    READ_MEASUREMENT: 1, GET_DATA_READY: 1, GET_ASC: 1, GET_SERIAL: 1,
    SET_ASC: 1, SET_TEMPERATURE_OFFSET: 1,
    STOP_PERIODIC: 500, PERSIST_SETTINGS: 800, REINIT: 30, FACTORY_RESET: 1200,
}

# Период измерения датчика в штатном режиме. Чаще опрашивать бессмысленно.
MEASUREMENT_PERIOD_S = 5


class ChecksumError(ValueError):
    """Слово не сошлось по CRC — на шине помехи или не тот адрес."""


def crc8(data: bytes) -> int:
    """CRC-8 по Sensirion: полином 0x31, начальное значение 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def frame(command: int, value: int | None = None) -> bytes:
    """Команда, при необходимости с аргументом и его контрольной суммой."""
    out = command.to_bytes(2, "big")
    if value is not None:
        payload = value.to_bytes(2, "big")
        out += payload + bytes([crc8(payload)])
    return out


def words(response: bytes) -> list[int]:
    """Ответ (по 2 байта данных + 1 байт CRC) -> список слов."""
    if len(response) % 3:
        raise ChecksumError(f"длина ответа {len(response)} не кратна трём")
    out = []
    for i in range(0, len(response), 3):
        chunk = response[i:i + 2]
        if crc8(chunk) != response[i + 2]:
            raise ChecksumError(f"слово {i // 3} не сошлось по CRC")
        out.append(int.from_bytes(chunk, "big"))
    return out


@dataclass
class Measurement:
    co2: int
    temperature: float
    humidity: float

    @property
    def plausible(self) -> bool:
        """Грубая проверка на вменяемость.

        Ноль ppm датчик отдаёт в первые секунды после старта, пока не готов
        первый замер, — такое значение нельзя ни показывать, ни писать в базу.
        """
        return 350 <= self.co2 <= 40000 and -10 <= self.temperature <= 60


def decode_measurement(response: bytes) -> Measurement:
    """Девять байт ответа -> человекочитаемые величины."""
    co2, raw_temp, raw_humidity = words(response)
    return Measurement(
        co2=co2,
        temperature=round(-45 + 175 * raw_temp / 65535, 2),
        humidity=round(100 * raw_humidity / 65535, 2),
    )


def data_ready(response: bytes) -> bool:
    """Готов ли свежий замер. Младшие 11 бит нулевые — ещё нет."""
    return bool(words(response)[0] & 0x07FF)


class Scd41:
    """Датчик поверх произвольного транспорта I2C.

    Транспорт — два вызова: write(address, bytes) и read(address, count).
    Так драйвер не привязан ни к smbus2, ни к чему-то ещё, и проверяется
    заглушкой.
    """

    def __init__(self, write, read, sleep=None) -> None:
        self._write = write
        self._read = read
        if sleep is None:
            import time
            sleep = time.sleep
        self._sleep = sleep

    def send(self, command: int, value: int | None = None) -> None:
        self._write(ADDRESS, frame(command, value))
        self._sleep(DELAYS_MS.get(command, 1) / 1000)

    def query(self, command: int, count: int) -> bytes:
        self.send(command)
        return self._read(ADDRESS, count)

    # ------------------------------------------------------------ рабочее

    def start(self) -> None:
        self.send(START_PERIODIC)

    def stop(self) -> None:
        self.send(STOP_PERIODIC)

    def ready(self) -> bool:
        return data_ready(self.query(GET_DATA_READY, 3))

    def read(self) -> Measurement:
        return decode_measurement(self.query(READ_MEASUREMENT, 9))

    # -------------------------------------------------------- настройка

    def set_auto_calibration(self, enabled: bool) -> None:
        """Отключать надо до start(): в периодическом режиме датчик
        настроечные команды игнорирует."""
        self.send(SET_ASC, 1 if enabled else 0)

    def get_auto_calibration(self) -> bool:
        return bool(words(self.query(GET_ASC, 3))[0])

    def set_temperature_offset(self, celsius: float) -> None:
        """Поправка на собственный нагрев.

        Плата греется сама, и в закрытом корпусе рядом с Pi покажет
        на пару градусов больше комнатной. Значение подбирается сверкой
        с референсным термометром в первый день (п.9 плана).
        """
        self.send(SET_TEMPERATURE_OFFSET, int(celsius * 65535 / 175))

    def persist(self) -> None:
        """Сохранить настройки в энергонезависимую память.

        Без этого отключение ASC не переживёт выключение питания. Вызывать
        редко: ресурс памяти конечен.
        """
        self.send(PERSIST_SETTINGS)

    def serial(self) -> str:
        return "".join(f"{w:04x}" for w in words(self.query(GET_SERIAL, 9)))
