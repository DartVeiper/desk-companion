"""Тесты протоколов датчиков. Железо не нужно: py app/drivers/test_drivers.py

Здесь проверяется всё, что можно проверить до пайки: разбор кадров радара,
антидребезг энкодера, контрольные суммы и пересчёт величин SCD41. На живом
железе останется только транспорт.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.drivers import ld2410, scd41, xpt2046
from app.drivers.encoder import (HOLD_MS, LOST_RELEASE_MS, SETTINGS_MS, STUCK_MS,
                                 ButtonDecoder, Encoder, QuadratureDecoder)
from app.inputs.events import Action, EventBus

failed = 0


def check(name: str, got, expected) -> None:
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name:48} {got}")


# ───────────────────────────────────────────────────────────── LD2410

def data_frame(payload: bytes) -> bytes:
    return (ld2410.DATA_HEAD + len(payload).to_bytes(2, "little")
            + payload + ld2410.DATA_TAIL)


def basic(state: int, moving_cm: int = 0, moving_e: int = 0,
          static_cm: int = 0, static_e: int = 0, detect_cm: int = 0) -> bytes:
    return bytes([ld2410.BASIC, 0xAA, state]) + \
        moving_cm.to_bytes(2, "little") + bytes([moving_e]) + \
        static_cm.to_bytes(2, "little") + bytes([static_e]) + \
        detect_cm.to_bytes(2, "little") + bytes([0x55, 0x00])


print("Радар LD2410: сборка потока")
frames, rest = ld2410.extract_frames(data_frame(basic(ld2410.MOVING, 120, 60)))
check("один целый кадр", len(frames), 1)
check("остатка нет", rest, b"")

stream = data_frame(basic(ld2410.MOVING)) + data_frame(basic(ld2410.STATIC))
frames, rest = ld2410.extract_frames(stream)
check("два кадра подряд", len(frames), 2)

half = data_frame(basic(ld2410.MOVING))
frames, rest = ld2410.extract_frames(half[:9])
check("кадр разрезан пополам — ждём хвост", len(frames), 0)
more, rest2 = ld2410.extract_frames(rest + half[9:])
check("склеился со следующей порцией", len(more), 1)

noisy = b"\x00\x11\x22" + data_frame(basic(ld2410.BOTH))
frames, _ = ld2410.extract_frames(noisy)
check("мусор перед заголовком отброшен", len(frames), 1)

bad_tail = bytearray(data_frame(basic(ld2410.MOVING)))
bad_tail[-1] = 0x00
frames, _ = ld2410.extract_frames(bytes(bad_tail))
check("битый хвост — кадр не принят", len(frames), 0)

print("\nРадар LD2410: разбор")
report = ld2410.decode(basic(ld2410.MOVING, moving_cm=175, moving_e=64, detect_cm=180))
check("движение распознано", report.state_name, "движение")
check("присутствие есть", report.present, True)
check("дистанция до движущейся цели", report.distance_cm, 175)
check("энергия движения", report.moving_energy, 64)

still = ld2410.decode(basic(ld2410.STATIC, static_cm=90, static_e=48))
check("статичное присутствие — тоже присутствие", still.present, True)
check("дистанция берётся статичная", still.distance_cm, 90)

empty = ld2410.decode(basic(ld2410.NO_TARGET))
check("пустая комната", empty.present, False)
check("дистанция ноль", empty.distance_cm, 0)

check("чужой кадр отброшен", ld2410.decode(b"\x07\xbb" + b"\x00" * 12), None)
check("слишком короткий отброшен", ld2410.decode(b"\x02\xaa\x01"), None)

eng_payload = (bytes([ld2410.ENGINEERING, 0xAA, ld2410.BOTH])
               + (150).to_bytes(2, "little") + bytes([70])
               + (100).to_bytes(2, "little") + bytes([55])
               + (160).to_bytes(2, "little")
               + bytes([8, 8])
               + bytes(range(10, 19)) + bytes(range(20, 29))
               + bytes([0x55, 0x00]))
eng = ld2410.decode(eng_payload)
check("инженерный режим опознан", eng.engineering, True)
check("ворот движения", len(eng.moving_gates), ld2410.GATES)
check("ворот статики", len(eng.static_gates), ld2410.GATES)
check("энергия по воротам движения", eng.moving_gates[:3], [10, 11, 12])
check("энергия по воротам статики", eng.static_gates[:3], [20, 21, 22])

print("\nРадар LD2410: команды")
check("кадр команды обрамлён верно",
      ld2410.ENABLE_CONFIG[:4] + ld2410.ENABLE_CONFIG[-4:],
      ld2410.CMD_HEAD + ld2410.CMD_TAIL)
check("длина в заголовке верна",
      int.from_bytes(ld2410.ENABLE_CONFIG[4:6], "little"),
      len(ld2410.ENABLE_CONFIG) - 10)
sens = ld2410.set_gate_sensitivity(3, moving=40, static=30)
check("чувствительность: слово команды", int.from_bytes(sens[6:8], "little"), 0x0064)
check("чувствительность: номер ворот", int.from_bytes(sens[10:14], "little"), 3)
check("чувствительность: движение", int.from_bytes(sens[16:20], "little"), 40)
check("чувствительность: статика", int.from_bytes(sens[22:26], "little"), 30)

# ─────────────────────────────────────────────────────────── энкодер

print("\nЭнкодер: вращение")


def turn(decoder: QuadratureDecoder, sequence: list[tuple[int, int]]) -> int:
    return sum(decoder.update(clk, dt) for clk, dt in sequence)


CW = [(1, 0), (1, 1), (0, 1), (0, 0)]   # один щелчок по часовой
CCW = [(0, 1), (1, 1), (1, 0), (0, 0)]  # один щелчок против

d = QuadratureDecoder()
check("щелчок по часовой", turn(d, CW), 1)
d = QuadratureDecoder()
check("щелчок против часовой", turn(d, CCW), -1)

d = QuadratureDecoder()
check("пять щелчков подряд", turn(d, CW * 5), 5)

d = QuadratureDecoder()
check("полщелчка — ещё не событие", turn(d, CW[:2]), 0)

d = QuadratureDecoder()
check("повтор того же состояния игнорируется",
      turn(d, [(0, 0), (0, 0), (0, 0)]), 0)

# Дребезг: контакт звенит, давая недопустимые переходы через диагональ.
d = QuadratureDecoder()
bouncy = [(1, 0), (0, 1), (1, 0), (1, 1), (0, 1), (0, 0)]
result = turn(d, bouncy)
check("дребезг не даёт лишних щелчков", abs(result) <= 1, True)
check("недопустимые переходы отброшены", d.rejected > 0, True)

d = QuadratureDecoder()
check("быстрое вращение не теряется", turn(d, CW * 20), 20)

# Пропущенный фронт: при быстром вращении обработчик читает выводы, когда
# оба уже сменились, и один переход из четырёх теряется. Щелчок обязан
# дойти всё равно — иначе человек крутит, а экран стоит.
d = QuadratureDecoder()
skipped = [(1, 0), (0, 1), (0, 0)]  # (1,1) потерян
check("щелчок с пропущенным фронтом доходит", turn(d, skipped), 1)

d = QuadratureDecoder()
check("пропуски подряд не накапливают ошибку", turn(d, skipped * 5), 5)

# Один дребезг посреди щелчка не должен стоить всего щелчка.
d = QuadratureDecoder()
glitched = [(1, 0), (1, 1), (0, 0), (0, 1), (0, 0)]
check("дребезг посреди щелчка не съедает его", turn(d, glitched), 1)

print("\nЭнкодер: кнопка")
b = ButtonDecoder()
b.press(1000)
check("короткое нажатие", b.release(1000 + HOLD_MS - 50), Action.SELECT)
b.press(2000)
check("удержание 0,7 с", b.release(2000 + HOLD_MS + 10), Action.HOLD)
b.press(3000)
check("удержание 3 с", b.release(3000 + SETTINGS_MS + 10), Action.SETTINGS)
check("отпускание без нажатия ничего не даёт", b.release(4000), None)
b.press(5000)
check("прогресс удержания виден", b.held_ms(5000 + 400), 400.0)

# Отпускание, проглоченное антидребезгом: ножка давно отпущена, а нажатие
# открыто — и полоса удержания висела бы на экране вечно.
b = ButtonDecoder()
b.press(6000)
check("ножка отпущена, событие ещё в пути — нажатие живо",
      b.held_ms(6100, pressed=False), 100.0)
check("отпущена дольше выдержки — отпускание потеряно",
      b.held_ms(6100 + LOST_RELEASE_MS, pressed=False), 0.0)
check("потеря посчитана", b.lost_releases, 1)
check("запоздавшее отпускание ничего не шлёт", b.release(6500), None)

b.press(7000)
b.held_ms(7050, pressed=False)
b.held_ms(7100, pressed=True)
check("дребезг посреди нажатия потерей не считается",
      b.held_ms(7100 + LOST_RELEASE_MS, pressed=False), float(LOST_RELEASE_MS + 100))
b.release(7400)

b = ButtonDecoder()
b.press(10000)
check("кнопку держат дольше предела — полоса пропадает",
      b.held_ms(10000 + STUCK_MS, pressed=True), 0.0)
check("нажатие помечено залипшим", b.stuck, True)
check("отпускание залипшей кнопки ничего не шлёт", b.release(10000 + STUCK_MS + 500), None)
check("метка снята", b.stuck, False)

print("\nЭнкодер: события в шину")
bus = EventBus()
enc = Encoder(bus)
for clk, dt in CW:
    enc.on_rotate(clk, dt)
check("поворот -> NEXT", bus.poll().action, Action.NEXT)
for clk, dt in CCW:
    enc.on_rotate(clk, dt)
check("обратный поворот -> PREV", bus.poll().action, Action.PREV)
enc.on_press(0)
enc.on_release(SETTINGS_MS + 100)
check("долгое удержание -> SETTINGS", bus.poll().action, Action.SETTINGS)

enc.read_pressed = lambda: False
enc.on_press(20000)
enc.held_ms(20010)
check("энкодер сверяет нажатие с ножкой", enc.held_ms(20010 + LOST_RELEASE_MS), 0.0)
check("и в шину ничего не уходит", bus.poll(), None)

# ────────────────────────────────────────────────────────────── SCD41

print("\nSCD41: контрольная сумма")
# Эталон из документации Sensirion: CRC для 0xBEEF равен 0x92.
check("эталонное значение из документации", hex(scd41.crc8(b"\xbe\xef")), "0x92")
check("CRC нулевого слова", scd41.crc8(b"\x00\x00"), 0x81)

print("\nSCD41: команды")
check("команда без аргумента", scd41.frame(scd41.READ_MEASUREMENT), b"\xec\x05")
asc_off = scd41.frame(scd41.SET_ASC, 0)
check("отключение ASC: команда", asc_off[:2], b"\x24\x16")
check("отключение ASC: аргумент с CRC", asc_off[2:], b"\x00\x00\x81")


def word(value: int) -> bytes:
    payload = value.to_bytes(2, "big")
    return payload + bytes([scd41.crc8(payload)])


print("\nSCD41: разбор ответа")
response = word(812) + word(25000) + word(30000)
m = scd41.decode_measurement(response)
check("CO2", m.co2, 812)
check("температура", m.temperature, round(-45 + 175 * 25000 / 65535, 2))
check("влажность", m.humidity, round(100 * 30000 / 65535, 2))
check("значение вменяемое", m.plausible, True)

zero = scd41.decode_measurement(word(0) + word(25000) + word(30000))
check("нулевой CO2 после старта отбраковывается", zero.plausible, False)

broken = bytearray(response)
broken[2] ^= 0xFF
try:
    scd41.decode_measurement(bytes(broken))
    check("битый CRC пойман", False, True)
except scd41.ChecksumError:
    check("битый CRC пойман", True, True)

try:
    scd41.words(b"\x00\x00")
    check("некратная длина поймана", False, True)
except scd41.ChecksumError:
    check("некратная длина поймана", True, True)

check("замер не готов", scd41.data_ready(word(0x8000)), False)
check("замер готов", scd41.data_ready(word(0x8007)), True)

print("\nSCD41: работа через заглушку транспорта")
log: list[tuple[str, object]] = []
queue = [word(700) + word(25000) + word(30000)]
sensor = scd41.Scd41(
    write=lambda addr, payload: log.append(("w", (addr, payload))),
    read=lambda addr, count: queue.pop(0),
    sleep=lambda _s: None,
)
sensor.set_auto_calibration(False)
sensor.persist()
sensor.start()
measurement = sensor.read()
check("на шину ушёл верный адрес", {a for _, (a, _) in log}, {scd41.ADDRESS})
check("порядок команд", [p[:2].hex() for _, (_, p) in log],
      ["2416", "3615", "21b1", "ec05"])
check("измерение прочитано", measurement.co2, 700)

# ──────────────────────────────────────────────────────────── XPT2046

print("\nТач XPT2046: пересчёт координат")
plain = xpt2046.Calibration(x_min=300, x_max=3800, y_min=300, y_max=3800,
                            swap_xy=False, invert_x=False, invert_y=False)
check("левый верхний угол", xpt2046.to_screen(300, 300, plain, 480, 320), (0, 0))
check("правый нижний угол", xpt2046.to_screen(3800, 3800, plain, 480, 320), (479, 319))
check("центр", xpt2046.to_screen(2050, 2050, plain, 480, 320), (240, 160))
check("выход за границы обрезается",
      xpt2046.to_screen(9999, -9999, plain, 480, 320), (479, 0))

flipped = xpt2046.Calibration(x_min=300, x_max=3800, y_min=300, y_max=3800,
                              swap_xy=False, invert_x=True, invert_y=True)
check("оба переворота", xpt2046.to_screen(300, 300, flipped, 480, 320), (479, 319))

swapped = xpt2046.Calibration(x_min=300, x_max=3800, y_min=300, y_max=3800,
                              swap_xy=True, invert_x=False, invert_y=False)
check("обмен осей", xpt2046.to_screen(3800, 300, swapped, 480, 320), (0, 319))

print("\nТач XPT2046: фильтрация и отбраковка ложных касаний")
check("медиана гасит выброс", xpt2046.median([2000, 2010, 3999, 1995, 2005]), 2005)
# Сопротивление обратно силе: жмут сильнее — значение меньше.
check("разомкнутая цепь — касания нет",
      xpt2046.touch_resistance(0, 500, 2000), xpt2046.NO_TOUCH)
# Живая панель в покое отдаёт z1 = 1..6, а вовсе не ноль, и x при этом
# равен нулю. Обе эти ситуации обязаны читаться как «не касаются»: из-за
# них экран листался сам.
check("слабый z1 — касания нет",
      xpt2046.touch_resistance(4, 4080, 0), xpt2046.NO_TOUCH)
check("нулевой x — касания нет",
      xpt2046.touch_resistance(3000, 3050, 0), xpt2046.NO_TOUCH)
check("покой живой панели читается как отсутствие касания",
      all(xpt2046.touch_resistance(z1, 4080, 0) == xpt2046.NO_TOUCH
          for z1 in range(1, 7)), True)
check("уверенное нажатие проходит порог",
      xpt2046.touch_resistance(3000, 3050, 2000) < xpt2046.MAX_RESISTANCE, True)
check("едва коснулись — отбраковано",
      xpt2046.touch_resistance(50, 3000, 2000) > xpt2046.MAX_RESISTANCE, True)
check("сильнее нажатие — меньше сопротивление",
      xpt2046.touch_resistance(3000, 3050, 2000)
      < xpt2046.touch_resistance(300, 3050, 2000), True)


print("\nТач XPT2046: калибровка по четырём углам")
# Модель панели с осями разной длины — как у настоящей. Старая калибровка
# на такой промахивалась мимо крестика на 35–43 пикселя: путала оси при
# swap_xy и не учитывала отступ крестиков от края. Проверяем не то, что
# границы как-то посчитаны, а главное: нажал в точку — попал в неё.
CAL_W, CAL_H, CAL_M = 480, 320, 34


def model_panel(swap, inv_x, inv_y):
    def press(px, py):
        fx, fy = px / (CAL_W - 1), py / (CAL_H - 1)
        if inv_x:
            fx = 1 - fx
        if inv_y:
            fy = 1 - fy
        a = 400 + fx * 3200   # канал экранной X
        b = 200 + fy * 3700   # канал экранной Y, длиннее
        return (round(b), round(a)) if swap else (round(a), round(b))
    return press


cal_corners = [(CAL_M, CAL_M), (CAL_W - CAL_M, CAL_M),
               (CAL_W - CAL_M, CAL_H - CAL_M), (CAL_M, CAL_H - CAL_M)]
cal_probes = [(CAL_M, CAL_M), (240, 160), (CAL_W - CAL_M, CAL_H - CAL_M),
              (5, 5), (474, 314), (100, 250)]
for swap in (True, False):
    for inv_x in (False, True):
        for inv_y in (False, True):
            panel_press = model_panel(swap, inv_x, inv_y)
            cal, _ = xpt2046.calibration_from_presses(
                [(c, panel_press(*c)) for c in cal_corners], CAL_W, CAL_H)
            miss = max(
                ((got[0] - p[0]) ** 2 + (got[1] - p[1]) ** 2) ** 0.5
                for p in cal_probes
                for got in [xpt2046.to_screen(*panel_press(*p), cal, CAL_W, CAL_H)])
            check(f"ориентация угадана: swap={swap} inv_x={inv_x} inv_y={inv_y}",
                  (cal.swap_xy, cal.invert_x, cal.invert_y), (swap, inv_x, inv_y))
            check(f"нажатие попадает в точку, включая края ({swap}/{inv_x}/{inv_y})",
                  miss < 1.5, True)

# Порядок нажатий не важен: углы узнаются по тому, куда целились.
reordered = [(c, model_panel(True, False, True)(*c)) for c in reversed(cal_corners)]
_, worst_reordered = xpt2046.calibration_from_presses(reordered, CAL_W, CAL_H)
check("порядок углов не важен", worst_reordered < 1.5, True)

try:
    xpt2046.calibration_from_presses(
        [((CAL_M, CAL_M), (1, 1))] * 4, CAL_W, CAL_H)
    check("четыре нажатия в один угол отвергнуты", False, True)
except ValueError:
    check("четыре нажатия в один угол отвергнуты", True, True)

print("\nТач XPT2046: чтение через заглушку")


def fake_transfer(payload: bytes) -> bytes:
    # 12-битный ответ выровнен влево в двух байтах после команды.
    # Z1 велико, Z2 близко к нему — это уверенное нажатие.
    value = {xpt2046.CMD_X: 2048, xpt2046.CMD_Y: 1024,
             xpt2046.CMD_Z1: 3000, xpt2046.CMD_Z2: 3050}[payload[0]]
    word16 = value << 3
    return bytes([0x00, word16 >> 8, word16 & 0xFF])


touch = xpt2046.Xpt2046(fake_transfer, swapped)
check("сырые координаты усреднены", touch.raw(), (2048, 1024))
check("касание распознано", touch.position() is not None, True)
check("координаты в пределах экрана",
      all(0 <= v < limit for v, limit in zip(touch.position(), (480, 320))), True)

print(f"\n  провалов: {failed}")
raise SystemExit(1 if failed else 0)
