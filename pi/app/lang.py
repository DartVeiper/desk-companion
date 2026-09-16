"""Язык экрана: русский или английский.

Перевод устроен не так, как обычно, и это стоит объяснить.

Обычный путь — обернуть каждую строку в вызов вида `t("Воздух")`. На наших
экранах таких мест около ста пятидесяти. Обёртки пришлось бы расставить
руками, а главное — расставлять их пришлось бы **каждому, кто добавит
экран**. Забыть одну означает выпустить блок, который на английском языке
показывает русское слово, и заметить это можно только глазами.

Хуже того, часть строк живёт не в вызовах, а в атрибутах класса и в
словарях на уровне модуля. Они вычисляются при импорте — то есть до того,
как язык вообще выбран, — и обёртка там просто заморозила бы русский.

Поэтому переводим в единственном месте, через которое проходит весь текст
на экране: в `draw.text`. Экраны рисуют как рисовали, ничего не зная про
язык, а `Translating` подменяет строку по дороге. Новый экран оказывается
переведённым сам, без единой строчки про перевод в нём.

Цена — строки, собранные из переменных: «6 ч без перерыва» в словаре не
найти. Их приходится собирать иначе — `t("{h} ч без перерыва").format(...)`,
— и таких мест полтора десятка, а не полтораста.

Чтобы про них нельзя было забыть, `Translating` запоминает всё русское,
что прошло мимо словаря. `tools/check_language.py` прогоняет экраны на
английском и печатает список — то есть непереведённое ищется прогоном, а
не вычитыванием кода.
"""

from __future__ import annotations

#: Какие языки знаем. Русский — исходный: его строки лежат прямо в коде,
#: поэтому словаря для него нет и не нужно.
LANGUAGES = ("ru", "en")

_language = "ru"

#: Что прошло мимо словаря, хотя выглядит русским. Наполняется только при
#: английском языке; читает check_language.py.
missed: set[str] = set()


def use(code: str) -> None:
    """Выбрать язык. Неизвестный — молча русский."""
    global _language
    _language = code if code in LANGUAGES else "ru"


def current() -> str:
    return _language


def has_cyrillic(text: str) -> bool:
    return any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in text)


def t(text: str, *, note: bool = True) -> str:
    """Перевести строку. Незнакомую возвращаем как есть.

    `note=False` — не записывать промах. Так меряют: обрезка по ширине
    перебирает укорачивающиеся куски, и каждый её шаг попадал бы в список
    непереведённого. Одна подпись давала восемьдесят пять мусорных строк,
    и настоящие терялись среди них.
    """
    if _language == "ru":
        return text
    found = _english(text)
    if found is not None:
        return found
    if note and has_cyrillic(text):
        missed.add(text)
    return text


def translate(text: str, code: str) -> str:
    """Перевести на заданный язык, не трогая выбранный для экрана.

    Нужно дашборду: приложение на ПК просит данные на своём языке, и
    переключать ради этого язык всего блока было бы неправильно — экран
    на секунду заговорил бы на чужом.
    """
    if code != "en":
        return text
    found = _english(text)
    return text if found is None else found


def _english(text: str) -> str | None:
    found = EN.get(text)
    if found is not None:
        return found
    # Шаблоны — только для кириллицы: цифры и английские строки идут через
    # draw.text десятки раз за кадр, и гонять по ним регулярки незачем.
    if not has_cyrillic(text):
        return None
    for pattern, template in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return template.format(**match.groupdict())
    return None


class Translating:
    """Обёртка над ImageDraw, переводящая текст по дороге.

    Всё остальное — линии, прямоугольники, овалы — отдаём как есть. Ради
    этого и обёртка, а не наследник: ImageDraw создаётся не нами, а PIL.
    """

    def __init__(self, draw) -> None:
        self._draw = draw

    def text(self, xy, text, *args, **kwargs):
        return self._draw.text(xy, t(str(text)), *args, **kwargs)

    # Меряем тоже переведённое: иначе подпись центрировалась бы по ширине
    # русского слова, а рисовалась английским — и уезжала бы вбок. Промахи
    # при этом не записываем, их запишет сам вызов text().
    def textlength(self, text, *args, **kwargs):
        return self._draw.textlength(t(str(text), note=False), *args, **kwargs)

    def textbbox(self, xy, text, *args, **kwargs):
        return self._draw.textbbox(xy, t(str(text), note=False), *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._draw, name)


def wrap(draw):
    """Обернуть, если есть что переводить. На русском — лишний слой."""
    return draw if _language == "ru" else Translating(draw)


#: Русский -> английский. Ключ — та самая строка, что лежит в коде экрана:
#: так перевод виден рядом с оригиналом, и не нужно выдумывать имена вроде
#: SCREEN_AIR_TITLE, по которым потом не найти, где это показывается.
#:
#: Полнота проверяется прогоном, а не глазами: tools/check_language.py
#: рисует все экраны на английском и печатает, что осталось русским.
EN: dict[str, str] = {
    # --------------------------------------------------- названия экранов
    "Часы + погода": "Clock and weather",
    "Погода подробно": "Weather in detail",
    "Воздух подробно": "Air in detail",
    "Прогноз": "Forecast",
    "Активность": "Activity",
    "Меня нет": "Away",
    "Аномалия": "Anomaly",
    "Стрик привычек": "Habit streak",
    "Ручной статус": "Manual status",
    "Настройки": "Settings",
    "Диагностика": "Diagnostics",
    "Яркость": "Brightness",
    "Ночной красный": "Night red",
    "Крупные цифры": "Big digits",
    "Точечная матрица": "Dot matrix",

    # ------------------------------------------------------ шапки экранов
    "Погода за окном": "Weather outside",
    "Воздух в комнате": "Room air",
    "Что будет дальше": "What is coming",
    "ПК-агент": "PC agent",

    # ------------------------------------------------------------- погода
    "ясно": "clear",
    "малооблачно": "mostly clear",
    "переменная облачность": "partly cloudy",
    "пасмурно": "overcast",
    "туман": "fog",
    "изморозь": "rime fog",
    "морось": "drizzle",
    "ледяная морось": "freezing drizzle",
    "дождь": "rain",
    "ливень": "showers",
    "сильный ливень": "heavy showers",
    "ледяной дождь": "freezing rain",
    "снег": "snow",
    "снегопад": "snowfall",
    "снежная крупа": "snow grains",
    "сильный снегопад с метелью": "heavy snow with drifting",
    "гроза": "thunderstorm",
    "гроза с градом": "thunderstorm with hail",
    "на улице": "outside",
    "в комнате": "indoors",
    "до дождя": "until rain",
    "данные на": "data as of",
    "температура": "temperature",
    "влажность": "humidity",
    "свежо": "fresh",
    "душно": "stuffy",
    "пора проветрить": "time to air out",
    "Нет данных": "No data",
    "Датчик молчит": "Sensor is silent",
    "Прогноза нет": "No forecast",
    "Pi не смог достучаться до погодного API": "Pi could not reach the weather API",
    "SCD41 не отвечает по I2C": "SCD41 is not answering on I2C",
    "осадков\nне ожидается": "no rain\nexpected",

    # ------------------------------------------------------- части суток
    # Коротко, без предлогов. По-русски «ночью» — одно слово, по-английски
    # «at night» уже два, а с «tomorrow» получалось «tomorrow at n…»: две
    # колонки прогноза читались одинаково и не различались вовсе.
    "ночью": "night",
    "утром": "morning",
    "днём": "afternoon",
    "вечером": "evening",

    # --------------------------------------------------------- дни недели
    "понедельник": "Monday",
    "вторник": "Tuesday",
    "среда": "Wednesday",
    "четверг": "Thursday",
    "пятница": "Friday",
    "суббота": "Saturday",
    "воскресенье": "Sunday",
    "пн": "Mon", "вт": "Tue", "ср": "Wed", "чт": "Thu",
    "пт": "Fri", "сб": "Sat", "вс": "Sun",

    # ------------------------------------------------------------- месяцы
    "января": "January", "февраля": "February", "марта": "March",
    "апреля": "April", "мая": "May", "июня": "June",
    "июля": "July", "августа": "August", "сентября": "September",
    "октября": "October", "ноября": "November", "декабря": "December",

    # ----------------------------------------------------------- за столом
    "за столом": "at the desk",
    "За столом": "At the desk",
    "никого": "nobody",
    "Скорее всего сплю": "Probably asleep",
    "радар видит присутствие": "radar sees someone",
    "радар никого не видит": "radar sees nobody",
    "статус только что сменился": "status just changed",
    "ручной статус": "manual status",
    "ручной статус не задан": "no manual status set",
    "в этом статусе": "in this status",
    "ночное окно": "night window",
    "авто": "auto",
    "Авто": "Auto",
    "Занят": "Busy", "занят": "busy",
    "На созвоне": "On a call", "на созвоне": "on a call",
    "Не беспокоить": "Do not disturb", "не беспокоить": "do not disturb",
    "Отошёл": "Stepped out", "отошёл": "stepped out",
    "Сплю": "Asleep", "сплю": "asleep",
    "активен": "active",

    # ----------------------------------------------------------- за компом
    "Код": "Code",
    "Игра": "Game",
    "Браузер": "Browser",
    "Прочее": "Other",
    "ПК офлайн": "PC offline",
    "нет связи": "no link",
    "звук": "sound",
    "вкл": "on",
    "выкл": "off",
    "нажатий за минуту": "keys per minute",
    "кликов за минуту": "clicks per minute",
    "heartbeat не приходит": "no heartbeat",
    "heartbeat не приходит — агент не запущен или сеть отвалилась":
        "no heartbeat — the agent is not running, or the network dropped",
    "CPU загрузка": "CPU load",
    "CPU темп.": "CPU temp",
    "GPU темп.": "GPU temp",
    "Датчики недоступны": "Sensors unavailable",
    "агенту нужны права администратора — проверь задачу в Планировщике":
        "the agent needs administrator rights — check its Scheduler task",
    "нет данных": "no data",
    "сейчас": "now",

    # ---------------------------------------------------------- привычки
    "перерыв каждые 2 часа": "a break every 2 hours",
    "день подряд": "day in a row",
    "дня подряд": "days in a row",
    "дней подряд": "days in a row",

    # ---------------------------------------------------------- аномалии
    "Всё как обычно": "Nothing unusual",
    "Необычная сессия": "Unusual session",
    "модель не видит отклонений": "the model sees no deviation",
    "модель считает на данных агента": "the model runs on the agent's data",
    "нет с чем сравнивать": "nothing to compare with",
    "непохоже на обычную сессию": "unlike a usual session",
    "почти без перерывов": "almost no breaks",
    "{value:.0f} мин без перерыва": "{value:.0f} min without a break",
    "втрое больше мыши, чем обычно": "three times the usual mouse",
    "необычно много ввода": "unusually heavy typing",
    "сидит, но почти не печатает": "sitting but barely typing",
    "дольше обычного за столом": "longer at the desk than usual",
    "заскочил ненадолго": "dropped in briefly",
    "ночное время": "night hours",
    "модель не обучена": "the model is not trained",

    # -------------------------------------------------------- диагностика
    "адрес": "address",
    "сигнал": "signal",
    "память": "memory",
    "карта": "card",
    "аптайм": "uptime",
    "версия": "version",
    "темп. Pi": "Pi temp",
    "питание": "power",
    "просадки": "throttling",
    "сбои ввода": "input glitches",
    "нет сети": "no network",
    "нет CO2": "no CO2",
    "норма": "normal",
    "мало места": "low space",
    "молчит": "silent",
    "отвечает": "answering",
    "не отвечает": "not answering",
    "работает": "running",
    "онлайн": "online",
    "офлайн": "offline",
    "время не сверено": "clock not synced",
    "Выйти": "Exit",
    "нажать": "press",
    "нажать — назад": "press to go back",
    "поворот — меняет": "turn to change",
    "ночью гаснет сама, если включён ночной режим":
        "dims itself at night when night mode is on",
    "реестр пуст: в конфиге не включён ни один экран":
        "registry is empty: no screen is enabled in the config",

    # ------------------------------------------------------------- покой
    "доброе утро": "good morning",
    "добрый день": "good afternoon",
    "добрый вечер": "good evening",
    "доброй ночи": "good night",
    "просыпаюсь": "waking up",

    # ------------------------------------------------- удержание и жесты
    "держите дальше — настройки": "keep holding for settings",
    "держите дальше — ручной статус": "keep holding for manual status",
    "можно отпускать": "you can let go",
    "отпустить сейчас —": "let go now —",
    "настройки": "settings",
    "обычное нажатие": "a normal press",

    # ---------------------------------------------- шаблоны с подстановкой
    # Строк, собранных из чисел, в словаре готовыми не удержать: их
    # бесконечно много. Держим шаблоны, а значения подставляются после
    # перевода — в коде экрана через .format().
    "{}с": "{}s",
    "{}м": "{}m",
    "{}ч {:02d}м": "{}h {:02d}m",
    "{}д {}ч": "{}d {}h",
    "{}, {} {}": "{}, {} {}",
    "{} ч без перерыва": "{} h without a break",
    "в комнате  {:.0f}%": "indoors  {:.0f}%",
    "дождь через {} мин": "rain in {} min",
    "{} мин": "{} min",
    "в комнате, на {:.0f}° теплее": "indoors, {:.0f}° warmer",
    "в комнате, на {:.0f}° холоднее": "indoors, {:.0f}° colder",
    "{} ч · {}-{}": "{} h · {}-{}",
    "{:.0f}% свободно": "{:.0f}% free",
    "{} из {} МБ": "{} of {} MB",
    "сброс через {}": "resets in {}",
    "   ·   на улице {:+.0f}°": "   ·   outside {:+.0f}°",
    "завтра {}": "tomorrow {}",
    # Название города подставляется после перевода и остаётся как есть:
    # переводится подпись, а не имя места.
    "Погода — {}": "Weather — {}",
    # ------------------------------------------ подписи в списке настроек
    # Дашборд называет экраны иначе, чем их заголовки на самом экране:
    # в списке нужна ясность, на экране — краткость.
    "Часы и погода": "Clock and weather",
    "Прогноз по частям суток": "Forecast by part of day",
    # -------------------------------------------------- неполадки блока
    # На экран блока выводятся только метки, а подробности — в приложение
    # и в дашборд. Поэтому прогон экранов их не ловил, и почти все они
    # оставались русскими.
    "нет брокера": "no broker",
    "нет радара": "no radar",
    "просадки напряжения — проверь блок и кабель":
        "undervoltage — check the power supply and the cable",
    "WiFi отвалился: нет времени по NTP и погоды":
        "Wi-Fi dropped: no network time and no weather",
    "mosquitto не отвечает — ПК-агент не достучится":
        "mosquitto is not responding — the PC agent cannot reach the board",
    "датчик воздуха замолчал — снять питание с платы физически, перезагрузка не помогает":
        "the air sensor went silent — cut power to the board physically, a reboot does not help",
    "LD2410 молчит по UART — присутствие не определяется":
        "LD2410 is silent on UART — presence is not detected",
    # --------------------------------------------------- калибровка тача
    "Калибровка экрана": "Touch calibration",
    "нажми точно в крестик": "press the centre of the cross",
    "кнопка энкодера — отмена": "encoder button cancels",
    "готово": "done",
    "не вышло": "failed",
    "отменено": "cancelled",
    "никто не нажимал три минуты": "no press for three minutes",
    "на этом блоке нет тача": "this device has no touch panel",
    "отменено из приложения": "cancelled from the app",
    "отменено кнопкой энкодера": "cancelled with the encoder button",
    "нужно четыре нажатия, по одному в каждый угол":
        "four presses are needed, one in each corner",
    "нажатия должны быть в разных углах": "the presses must be in different corners",
}

#: Строки, в которые вписано число. Словарём их не найти: ключом была бы
#: «на карте свободно 5%», а завтра там будет 4. Большинство таких мест
#: переводят шаблон до подстановки — `t("{} мин").format(...)`, — но
#: неполадки собираются там, где язык ещё неизвестен, и приходят готовыми.
#:
#: Имя в фигурных скобках ловит любой текст и переносится в перевод.
TEMPLATES: dict[str, str] = {
    "на карте свободно {pct}%": "{pct}% free on the card",
    # Калибровка тача: итог собирается из промаха в пикселях.
    "точность {px} px": "accuracy {px} px",
    "нажатие ушло мимо крестика на {px} px — попробуй ещё раз":
        "a press missed its cross by {px} px — try again",
    "не записалось: {error}": "could not save: {error}",
}


def _compile(template: str):
    import re

    parts = re.split(r"\{(\w+)\}", template)
    # Чётные куски — текст как есть, нечётные — имена подстановок.
    pattern = "".join(re.escape(part) if i % 2 == 0 else f"(?P<{part}>.+?)"
                      for i, part in enumerate(parts))
    return re.compile(pattern)


_PATTERNS = [(_compile(source), target) for source, target in TEMPLATES.items()]
