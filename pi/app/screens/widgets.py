"""Общие элементы оформления.

Все семь режимов собираются из этих кирпичей, поэтому выглядят одним
устройством, а не набором случайных экранов. Правка вида — здесь и в
theme.py, в сами экраны лезть не надо.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme

Box = tuple[float, float, float, float]
Color = tuple[int, int, int]

PAD = 14
GAP = 10


def status_badge(draw: ImageDraw.ImageDraw, width: int, state) -> float:
    """Значок неисправности в правом верхнем углу. Возвращает занятую ширину.

    Пока всё в порядке — не рисует ничего. В этом весь смысл: значок
    замечается именно потому, что обычно его нет, и место на экране под
    постоянную панель здоровья не тратится.
    """
    problems = state.problems()
    if not problems:
        return 0.0

    label, _, critical = problems[0]
    if len(problems) > 1:
        label += f"  +{len(problems) - 1}"
    color = theme.ALERT if critical else theme.WARN

    font = theme.font(theme.TINY, bold=True)
    text_w = draw.textlength(label, font=font)
    right = width - theme.PAD
    draw.text((right, 32), label, font=font, fill=color, anchor="rm")
    draw.ellipse((right - text_w - 16, 27, right - text_w - 6, 37), fill=color)
    return text_w + 22


#: Сторона кнопки «домой». Размер под палец, а не под курсор: 44 точки —
#: примерно ширина подушечки, и меньше делать нельзя, иначе мимо попадают
#: чаще, чем в цель.
HOME_SIZE = 44


def home_box(width: int) -> Box:
    """Где находится кнопка «домой».

    Отдельной функцией, потому что её должны знать двое: отрисовка и
    разбор тапа. Разъехавшись, они дали бы кнопку, которая видна, но не
    нажимается — и искать такое пришлось бы долго.
    """
    return (float(width - theme.PAD - HOME_SIZE), 10.0,
            float(width - theme.PAD), 10.0 + HOME_SIZE)


def home_button(draw: ImageDraw.ImageDraw, width: int) -> Box:
    """Стрелка возврата на первый экран.

    Рисуется только на накладках: из карусели выходить некуда, а лишняя
    кнопка на каждом экране съедала бы и место, и внимание.
    """
    box = home_box(width)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    draw.rounded_rectangle(box, radius=10, fill=theme.SURFACE)
    # Стрелку рисуем линиями, а не символом из шрифта: в Nunito стрелок
    # нет, и вместо неё встал бы квадрат.
    draw.line((cx - 8, cy, cx + 9, cy), fill=theme.ACCENT, width=3)
    draw.line((cx - 8, cy, cx - 1, cy - 7), fill=theme.ACCENT, width=3)
    draw.line((cx - 8, cy, cx - 1, cy + 7), fill=theme.ACCENT, width=3)
    return box


def header(
    draw: ImageDraw.ImageDraw,
    width: int,
    title: str,
    right: str | None = None,
    dot: Color | None = None,
    state=None,
    home: bool = False,
) -> None:
    """Верхняя строка: название режима слева, статус справа.

    Если передать state, справа появится значок неисправности — и только
    при наличии таковой.
    """
    x = theme.PAD
    if dot is not None:
        draw.ellipse((x, 27, x + 10, 37), fill=dot)
        x += 20
    draw.text((x, 32), title, font=theme.font(theme.SMALL, bold=True), fill=theme.FG, anchor="lm")

    if home:
        # Кнопка занимает правый угол целиком, поэтому подпись и значок
        # неисправности туда уже не помещаются — и не нужны: на накладке
        # смотрят на содержимое, а не на состояние блока.
        home_button(draw, width)
        return

    taken = status_badge(draw, width, state) if state is not None else 0.0
    if right:
        draw.text((width - theme.PAD - taken, 32), right,
                  font=theme.font(theme.SMALL), fill=theme.DIM, anchor="rm")


def hold_overlay(frame: Image.Image, held_ms: float) -> None:
    """Прогресс удержания поверх текущего экрана.

    Без него жест вслепую: классифицируем-то мы по отпусканию, значит до
    самого отпускания человек не знает, работает ли вообще. Заодно жест
    становится обнаружимым, а не секретным.

    Экран под панелью гасим — иначе полоса сливается с карточками и читается
    как часть режима, а не как временная подсказка.
    """
    from ..inputs.gestures import HOLD_MS, SETTINGS_MS

    # Порог заметно выше времени осознанного клика: быстрое нажатие
    # длится 80-200 мс, и панель, мелькнувшая на нём, только мешает —
    # особенно когда через меню идут быстро и подряд.
    if held_ms < 400:
        return

    # Шкала одна на весь жест, от нуля до трёх секунд, и назад не ходит.
    # Раньше полоса считала до первого порога, заполнялась, сбрасывалась на
    # четверть и считала заново до второго — два жеста накладывались на одну
    # полосу, и это читалось как сбой, а не как две ступени.
    # Ступень теперь показана засечкой на самой шкале.
    if held_ms < HOLD_MS:
        label, color = "обычное нажатие", theme.DIM
        hint = "держите дальше — ручной статус"
    elif held_ms < SETTINGS_MS:
        label, color = "ручной статус", theme.WARN
        hint = "держите дальше — настройки"
    else:
        label, color = "настройки", theme.ACCENT
        hint = "можно отпускать"

    width, height = frame.size
    frame.paste(Image.blend(frame.copy(), Image.new("RGB", frame.size, theme.BG), 0.72), (0, 0))
    draw = ImageDraw.Draw(frame)

    cy = height / 2
    panel = (PAD + 40, cy - 54, width - PAD - 40, cy + 62)
    draw.rounded_rectangle(panel, radius=16, fill=theme.SURFACE, outline=color, width=2)
    draw.text((width / 2, cy - 34), "отпустить сейчас —",
              font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")
    draw.text((width / 2, cy - 8), label, font=theme.font(theme.H2, bold=True),
              fill=color, anchor="mm")

    bar = (panel[0] + 28, cy + 20, panel[2] - 28, cy + 28)
    span = bar[2] - bar[0]
    draw.rounded_rectangle(bar, radius=4, fill=theme.LINE)

    filled = min(1.0, held_ms / SETTINGS_MS)
    if filled > 0.02:
        draw.rounded_rectangle((bar[0], bar[1], bar[0] + span * filled, bar[3]),
                               radius=4, fill=color)

    # Засечка на первом пороге: видно, что ступени две и где вторая.
    mark = bar[0] + span * (HOLD_MS / SETTINGS_MS)
    draw.line((mark, bar[1] - 4, mark, bar[3] + 4),
              fill=theme.FG if held_ms >= HOLD_MS else theme.DIM, width=2)

    draw.text((width / 2, cy + 46), hint,
              font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")


def splash(width: int, height: int, note: str = "") -> Image.Image:
    """Кадр «блок просыпается».

    Нужен потому, что от подачи питания до первых данных проходят секунды:
    грузится система, поднимается сеть, опрашиваются датчики. Тёмный экран
    всё это время читается как «не включилось» — человек тянется выдернуть
    питание ровно тогда, когда всё идёт нормально.
    """
    frame = Image.new("RGB", (width, height), theme.BG)
    draw = ImageDraw.Draw(frame)

    draw.text((width / 2, height / 2 - 26), "Desk Companion",
              font=theme.font(theme.H1, bold=True), fill=theme.FG, anchor="mm")
    draw.text((width / 2, height / 2 + 10), note or "просыпаюсь",
              font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")

    # Полоса не показывает прогресс: его неоткуда взять, а врущий индикатор
    # хуже честного отсутствия. Она просто говорит, что блок жив.
    bar = (width / 2 - 90, height / 2 + 44, width / 2 + 90, height / 2 + 50)
    draw.rounded_rectangle(bar, radius=3, fill=theme.LINE)
    draw.rounded_rectangle((bar[0], bar[1], bar[0] + 60, bar[3]),
                           radius=3, fill=theme.ACCENT)
    return frame


def card(draw: ImageDraw.ImageDraw, box: Box, fill: Color | None = None) -> None:
    draw.rounded_rectangle(box, radius=14, fill=fill or theme.SURFACE)


def note(draw: ImageDraw.ImageDraw, x: float, y: float, color, height: float = 13) -> float:
    """Нотка из примитивов. Возвращает свою ширину.

    Рисуем, а не берём символ из шрифта. Проверено: ♪ в Nunito нет, и
    Pillow подставляет вместо него пустой квадратик «глифа не найдено».
    Причём молча — отпечаток непустой, вёрстка сходится, тесты проходят, и
    увидеть подмену можно только глазами на живом экране.
    """
    width = height * 0.62
    stem = max(1.5, height * 0.11)
    head = height * 0.42
    # Головка внизу, палочка справа вверх — как у восьмой ноты.
    draw.ellipse((x, y + height - head, x + head * 1.25, y + height), fill=color)
    draw.rectangle((x + head * 1.25 - stem, y, x + head * 1.25, y + height - head * 0.45),
                   fill=color)
    return width


def ellipsize(draw: ImageDraw.ImageDraw, text: str, max_width: float, font) -> str:
    """Обрезать по ширине, поставив многоточие.

    Уменьшать шрифт тут нельзя до бесконечности: подпись «сильный снегопад
    с метелью» под карточкой ужалась бы до нечитаемой. Лучше честно
    показать начало и дать понять, что дальше есть ещё.
    """
    if draw.textlength(text, font=font) <= max_width:
        return text
    cut = text
    while cut and draw.textlength(cut + "…", font=font) > max_width:
        cut = cut[:-1]
    return (cut.rstrip() + "…") if cut else ""


def wrap(draw: ImageDraw.ImageDraw, text: str, max_width: float, font,
         max_lines: int = 2) -> list[str]:
    """Разложить по словам в строки нужной ширины.

    Последняя строка при нехватке места обрезается многоточием — иначе
    длинное объяснение молча уезжает за край экрана.
    """
    words, lines, line = text.split(), [], ""
    for word in words:
        probe = f"{line} {word}".strip()
        if draw.textlength(probe, font=font) <= max_width or not line:
            line = probe
        else:
            lines.append(line)
            line = word
            if len(lines) == max_lines:
                break
    if line and len(lines) < max_lines:
        lines.append(line)
    if len(lines) == max_lines:
        used = len(" ".join(lines).split())
        if used < len(words):
            lines[-1] = ellipsize(draw, lines[-1] + " " + words[used], max_width, font)
    return lines


def stat_card(
    draw: ImageDraw.ImageDraw,
    box: Box,
    value: str,
    caption: str,
    color: Color = theme.FG,
    value_size: int = 34,
    icon=None,
    icon_size: float = 20,
) -> None:
    """Карточка «крупное значение + подпись под ним».

    icon — необязательная рисовалка вида icon(draw, x, y, size). Значок
    встаёт перед подписью, и пара центрируется целиком. Не над значением:
    там для него нет места, крупное число занимает карточку по ширине.
    """
    card(draw, box)
    cx = (box[0] + box[2]) / 2
    draw.text((cx, box[1] + 38), value, font=theme.font(value_size, bold=True),
              fill=color, anchor="mm")

    caption_font = theme.font(theme.TINY)
    room = box[2] - box[0] - 16
    row = box[3] - 22
    if icon is None:
        draw.text((cx, row), ellipsize(draw, caption, room, caption_font),
                  font=caption_font, fill=theme.DIM, anchor="mm")
        return

    gap = 5
    text = ellipsize(draw, caption, room - icon_size - gap, caption_font)
    span = icon_size + gap + draw.textlength(text, font=caption_font)
    left = cx - span / 2
    icon(draw, left, row - icon_size / 2, icon_size)
    draw.text((left + icon_size + gap, row), text,
              font=caption_font, fill=theme.DIM, anchor="lm")


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: float,
    size: int,
    bold: bool = True,
    min_size: int = 12,
):
    """Шрифт, при котором текст влезает в ширину.

    Подписи приходят разной длины («Занят» против «Не беспокоить»), и
    подбирать размер на глаз под самую длинную — значит мельчить все.
    """
    while size > min_size:
        font = theme.font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return theme.font(min_size, bold=bold)


def check_mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, color: Color) -> None:
    """Галочка линиями, а не символом: в DejaVu и Segoe UI глифа ✓ может не
    быть, и вместо него рисуется пустой квадрат."""
    draw.line(
        (cx - size * 0.5, cy, cx - size * 0.1, cy + size * 0.42,
         cx + size * 0.55, cy - size * 0.45),
        fill=color, width=max(2, int(size * 0.2)), joint="curve",
    )


def row(width: int, top: float, bottom: float, count: int, pad: float = PAD, gap: float = GAP) -> list[Box]:
    """Ряд из count карточек по всей ширине."""
    cell = (width - pad * 2 - gap * (count - 1)) / count
    return [(pad + i * (cell + gap), top, pad + i * (cell + gap) + cell, bottom)
            for i in range(count)]


def empty_state(draw: ImageDraw.ImageDraw, width: int, height: int, text: str, hint: str = "") -> None:
    """Экран без данных. Показываем причину, а не пустоту.

    По п.6 плана Режим 5 без прав администратора молча остаётся пустым —
    вот чтобы это «молча» не случилось, здесь всегда есть текст.
    """
    draw.text((width / 2, height / 2 - 12), text,
              font=theme.font(theme.H2, bold=True), fill=theme.DIM, anchor="mm")
    if hint:
        font = theme.font(theme.SMALL)
        lines = wrap(draw, hint, width - PAD * 2 - 20, font)
        for i, line in enumerate(lines):
            draw.text((width / 2, height / 2 + 26 + i * 20), line,
                      font=font, fill=theme.LINE, anchor="mm")


def duration(seconds: float) -> str:
    """Длительность коротко: 2ч 15м, 45м, 30с."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}м"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}ч {minutes:02d}м"
    return f"{hours // 24}д {hours % 24}ч"
