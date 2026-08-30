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


def header(
    draw: ImageDraw.ImageDraw,
    width: int,
    title: str,
    right: str | None = None,
    dot: Color | None = None,
    state=None,
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

    if held_ms < 120:  # случайное касание не должно мигать полосой
        return

    if held_ms < HOLD_MS:
        target, label, color = HOLD_MS, "ручной статус", theme.DIM
    elif held_ms < SETTINGS_MS:
        target, label, color = SETTINGS_MS, "ручной статус", theme.WARN
    else:
        target, label, color = SETTINGS_MS, "настройки", theme.ACCENT

    width, height = frame.size
    frame.paste(Image.blend(frame.copy(), Image.new("RGB", frame.size, theme.BG), 0.72), (0, 0))
    draw = ImageDraw.Draw(frame)

    cy = height / 2
    panel = (PAD + 40, cy - 46, width - PAD - 40, cy + 46)
    draw.rounded_rectangle(panel, radius=16, fill=theme.SURFACE, outline=color, width=2)
    draw.text((width / 2, cy - 22), "продолжайте держать",
              font=theme.font(theme.TINY), fill=theme.DIM, anchor="mm")
    draw.text((width / 2, cy + 6), label, font=theme.font(theme.H2, bold=True),
              fill=color, anchor="mm")

    bar = (panel[0] + 28, cy + 30, panel[2] - 28, cy + 38)
    draw.rounded_rectangle(bar, radius=4, fill=theme.LINE)
    filled = min(1.0, held_ms / target)
    if filled > 0.02:
        draw.rounded_rectangle((bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * filled, bar[3]),
                               radius=4, fill=color)


def card(draw: ImageDraw.ImageDraw, box: Box, fill: Color | None = None) -> None:
    draw.rounded_rectangle(box, radius=14, fill=fill or theme.SURFACE)


def stat_card(
    draw: ImageDraw.ImageDraw,
    box: Box,
    value: str,
    caption: str,
    color: Color = theme.FG,
    value_size: int = 34,
) -> None:
    """Карточка «крупное значение + подпись под ним»."""
    card(draw, box)
    cx = (box[0] + box[2]) / 2
    draw.text((cx, box[1] + 38), value, font=theme.font(value_size, bold=True),
              fill=color, anchor="mm")
    draw.text((cx, box[3] - 22), caption, font=theme.font(theme.TINY),
              fill=theme.DIM, anchor="mm")


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
        draw.text((width / 2, height / 2 + 26), hint,
                  font=theme.font(theme.SMALL), fill=theme.LINE, anchor="mm")


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
