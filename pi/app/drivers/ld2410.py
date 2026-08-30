"""Разбор протокола радара LD2410 (24 ГГц, присутствие).

Транспорта здесь нет — только байты. Поэтому весь разбор проверяется
тестами до того, как модуль вообще припаян, а на живом железе останется
отладить один UART.

ВАЖНО: раскладка полей взята по документации на LD2410/LD2410B. Ревизии
платы отличаются числом ворот и наличием инженерного режима, так что
первое, что надо сделать с живым модулем — снять сырой поток и сверить
с ожидаемым (см. tools/ld2410_sniff.py).

Формат кадра данных:
    F4 F3 F2 F1 | длина (2, LE) | содержимое | F8 F7 F6 F5
Формат командного кадра:
    FD FC FB FA | длина (2, LE) | содержимое | 04 03 02 01
"""

from __future__ import annotations

from dataclasses import dataclass, field

DATA_HEAD = b"\xf4\xf3\xf2\xf1"
DATA_TAIL = b"\xf8\xf7\xf6\xf5"
CMD_HEAD = b"\xfd\xfc\xfb\xfa"
CMD_TAIL = b"\x04\x03\x02\x01"

BASIC, ENGINEERING = 0x02, 0x01
GATES = 9  # ворота 0..8, шаг примерно 0.75 м

# Состояние цели в первом байте полезной части.
NO_TARGET, MOVING, STATIC, BOTH = 0x00, 0x01, 0x02, 0x03

_STATE_NAMES = {
    NO_TARGET: "никого", MOVING: "движение",
    STATIC: "статично", BOTH: "движение+статика",
}


@dataclass
class Report:
    """Один отсчёт радара."""

    target_state: int = NO_TARGET
    moving_distance_cm: int = 0
    moving_energy: int = 0
    static_distance_cm: int = 0
    static_energy: int = 0
    detection_distance_cm: int = 0
    engineering: bool = False
    #: Энергия по воротам дальности. Пусто в базовом режиме.
    moving_gates: list[int] = field(default_factory=list)
    static_gates: list[int] = field(default_factory=list)
    max_moving_gate: int = 0
    max_static_gate: int = 0

    @property
    def present(self) -> bool:
        """Человек есть, в том числе неподвижный.

        Ровно ради статичного присутствия LD2410 и выбран: обычный
        доплеровский модуль сидящего без движения человека не видит,
        и Режим 3 на нём не работал бы (п.10 плана).
        """
        return self.target_state != NO_TARGET

    @property
    def state_name(self) -> str:
        return _STATE_NAMES.get(self.target_state, f"неизвестно ({self.target_state:#04x})")

    @property
    def distance_cm(self) -> int:
        """До чего мерить дистанцию: до движущейся цели, иначе до статичной."""
        if self.target_state in (MOVING, BOTH):
            return self.moving_distance_cm
        if self.target_state == STATIC:
            return self.static_distance_cm
        return 0


def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Вынуть завершённые кадры данных из потока.

    Возвращает (кадры, остаток). Остаток скармливается обратно вместе со
    следующей порцией: UART отдаёт байты как попало, и кадр запросто
    приезжает разрезанным пополам.
    """
    frames: list[bytes] = []
    while True:
        start = buffer.find(DATA_HEAD)
        if start < 0:
            # Мусор до заголовка выбрасываем, но хвост длиной с заголовок
            # оставляем: в нём может лежать начало следующего кадра.
            return frames, buffer[-(len(DATA_HEAD) - 1):] if buffer else b""
        buffer = buffer[start:]
        if len(buffer) < 6:
            return frames, buffer
        length = int.from_bytes(buffer[4:6], "little")
        end = 6 + length + len(DATA_TAIL)
        if len(buffer) < end:
            return frames, buffer
        if buffer[6 + length:end] != DATA_TAIL:
            buffer = buffer[len(DATA_HEAD):]  # ложный заголовок, ищем дальше
            continue
        frames.append(buffer[6:6 + length])
        buffer = buffer[end:]


def decode(payload: bytes) -> Report | None:
    """Разобрать полезную часть кадра. None — кадр не тот или битый."""
    if len(payload) < 13 or payload[1] != 0xAA:
        return None

    kind = payload[0]
    if kind not in (BASIC, ENGINEERING):
        return None

    report = Report(
        target_state=payload[2],
        moving_distance_cm=int.from_bytes(payload[3:5], "little"),
        moving_energy=payload[5],
        static_distance_cm=int.from_bytes(payload[6:8], "little"),
        static_energy=payload[8],
        detection_distance_cm=int.from_bytes(payload[9:11], "little"),
        engineering=kind == ENGINEERING,
    )
    if not report.engineering:
        return report

    # Инженерный режим: энергия по каждым воротам отдельно для движения и
    # статики. Именно эти два массива нужны панели калибровки — без них
    # подбор порогов превращается в гадание (п.9 плана).
    body = payload[11:]
    if len(body) < 2 + 2 * GATES:
        return report
    report.max_moving_gate = body[0]
    report.max_static_gate = body[1]
    report.moving_gates = list(body[2:2 + GATES])
    report.static_gates = list(body[2 + GATES:2 + 2 * GATES])
    return report


def command(word: int, value: bytes = b"") -> bytes:
    """Собрать командный кадр."""
    body = word.to_bytes(2, "little") + value
    return CMD_HEAD + len(body).to_bytes(2, "little") + body + CMD_TAIL


# Команды, которые реально нужны. Настройку чувствительности шлём только
# внутри «конфигурации»: вне её модуль команды игнорирует.
ENABLE_CONFIG = command(0x00FF, b"\x01\x00")
END_CONFIG = command(0x00FE)
ENABLE_ENGINEERING = command(0x0062)
DISABLE_ENGINEERING = command(0x0063)
READ_PARAMS = command(0x0061)
RESTART = command(0x00A3)


def set_gate_sensitivity(gate: int, moving: int, static: int) -> bytes:
    """Порог чувствительности для одних ворот, 0..100.

    Ворота 0 и 1 статику не поддерживают — у них слишком малая дальность,
    и значение статики модуль проигнорирует.
    """
    value = (
        b"\x00\x00" + gate.to_bytes(4, "little")
        + b"\x01\x00" + moving.to_bytes(4, "little")
        + b"\x02\x00" + static.to_bytes(4, "little")
    )
    return command(0x0064, value)


def set_max_gates(moving_gate: int, static_gate: int, idle_seconds: int) -> bytes:
    """Дальность и время удержания.

    idle_seconds — сколько модуль продолжает считать, что человек здесь,
    после пропадания сигнала. Это первый параметр, который придётся
    крутить: короткое значение даёт мигание присутствия у неподвижно
    сидящего человека.
    """
    value = (
        b"\x00\x00" + moving_gate.to_bytes(4, "little")
        + b"\x01\x00" + static_gate.to_bytes(4, "little")
        + b"\x02\x00" + idle_seconds.to_bytes(4, "little")
    )
    return command(0x0060, value)
