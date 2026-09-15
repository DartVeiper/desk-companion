"""Экраны подробностей: открываются тапом по карточке или нажатием.

Закрываются любым действием, кроме поворота — поворот листает подробности
одного и того же режима. Залипнуть здесь нельзя, выхода искать не надо.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import theme
from ..sources.desk import EnvTrendSource
from . import weather_icons as icons
from ..state import State
from . import widgets as w
from .base import DetailScreen

BACK_HINT = "нажать — назад"


class WeatherDetail(DetailScreen):
    name = "weather_detail"
    title = "Погода подробно"

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        out = state.weather
        # Город в заголовке, когда он назван. Без него «+12, пасмурно» — это
        # погода неизвестно где: собравший себе такой же блок первым делом
        # хочет убедиться, что видит свою погоду, а не чужую.
        w.header(draw, width, f"Погода — {out.place}" if out.place else "Погода за окном",
                 dot=theme.ACCENT, home=True)

        if out.temp is None:
            w.empty_state(draw, width, height, "Нет данных",
                          "Pi не смог достучаться до погодного API")
            return

        draw.text((theme.PAD + 6, 128), f"{out.temp:+.0f}°",
                  font=theme.font(96, weight=theme.EXTRABOLD), fill=theme.FG, anchor="ls")
        # Значок рядом со словом, под температурой. Правее нельзя: там
        # живёт блок осадков во всю высоту, и крупный значок наезжал на
        # него — это поймал обход вёрстки.
        icons.draw_icon(draw, theme.PAD + 2, 140, 46, out.code, out.is_day,
                        back=theme.BG)
        # Правее 238 начинается блок осадков, поэтому слово обрезаем по
        # оставшемуся месту: «сильный снегопад с метелью» иначе въезжает
        # прямо в него.
        cond_font = theme.font(theme.BODY)
        cond_left = theme.PAD + 54
        draw.text((cond_left, 162),
                  w.ellipsize(draw, out.cond or "—", 230 - cond_left, cond_font),
                  font=cond_font, fill=theme.DIM, anchor="lm")

        if out.rain_soon_minutes is not None:
            box = (238, 74, width - w.PAD, 178)
            w.card(draw, box, (22, 38, 58))
            draw.text(((box[0] + box[2]) / 2, box[1] + 38), f"{out.rain_soon_minutes} мин",
                      font=theme.font(42, bold=True), fill=theme.ACCENT, anchor="mm")
            draw.text(((box[0] + box[2]) / 2, box[3] - 26), "до дождя",
                      font=theme.font(theme.SMALL), fill=theme.DIM, anchor="mm")
        else:
            draw.text((238, 126), "осадков\nне ожидается",
                      font=theme.font(theme.BODY), fill=theme.DIM, spacing=8)

        boxes = w.row(width, 196, height - w.PAD, 2)
        room = state.env.temperature
        # Показываем температуру комнаты, а разницу уносим в подпись.
        # Раньше в карточке стояла сама разница — крупное «+16°» под
        # словами «теплее, чем на улице». Два абсолютных числа читаются
        # однозначно всегда, одинокая разница — нет: её принимают за
        # температуру, и экран начинает врать, не сказав ни слова неправды.
        if room is None:
            w.stat_card(draw, boxes[0], "—", "в комнате")
        else:
            w.stat_card(draw, boxes[0], f"{room:.1f}°",
                        f"в комнате, на {room - out.temp:.0f}° теплее"
                        if room >= out.temp else
                        f"в комнате, на {out.temp - room:.0f}° холоднее")
        w.stat_card(draw, boxes[1], state.now.strftime("%H:%M"), "данные на")


class AirDetail(DetailScreen):
    name = "air_detail"
    title = "Воздух подробно"

    # Пороги те же, что красят число на Режиме 1. Потом уедут в настройки (п.7).
    SCALE_MIN, SCALE_MAX = 400, 2000

    def render(self, state: State, draw: ImageDraw.ImageDraw, frame: Image.Image) -> None:
        width, height = frame.size
        env = state.env
        w.header(draw, width, "Воздух в комнате", dot=theme.co2_color(env.co2), home=True)

        if env.co2 is None:
            w.empty_state(draw, width, height, "Датчик молчит",
                          "SCD41 не отвечает по I2C")
            return

        number_font = theme.font(88, weight=theme.EXTRABOLD)
        draw.text((theme.PAD + 6, 130), str(env.co2), font=number_font,
                  fill=theme.co2_color(env.co2), anchor="ls")
        draw.text((theme.PAD + 10, 160), "ppm CO2",
                  font=theme.font(theme.SMALL), fill=theme.DIM, anchor="lm")

        # Ширину под вердикт считаем от фактической ширины числа: у
        # четырёхзначного CO2 её остаётся заметно меньше, чем у трёхзначного.
        verdict = self._verdict(env.co2)
        used = theme.PAD + 6 + draw.textlength(str(env.co2), font=number_font)
        draw.text((width - theme.PAD, 126), verdict,
                  font=w.fit_font(draw, verdict, width - theme.PAD - used - 18, theme.H2),
                  fill=theme.co2_color(env.co2), anchor="rs")

        # Ход за последние часы — в свободной полосе справа вверху, под
        # кнопкой возврата и над вердиктом. Число говорит, сколько сейчас;
        # линия — растёт оно или падает, а проветривают именно по этому.
        if len(env.trend) >= 2:
            # Подпись над графиком, а не под ним: под ним живёт вердикт
            # крупным шрифтом, и «пора проветрить» её перекрывало.
            draw.text((246, 46),
                      f"{EnvTrendSource.HOURS} ч · {min(env.trend)}-{max(env.trend)}",
                      font=theme.font(theme.TINY), fill=theme.DIM, anchor="lm")
            w.sparkline(draw, (246, 60, width - theme.PAD, 100), env.trend,
                        theme.co2_color(env.co2))

        self._scale(draw, env.co2, (theme.PAD, 186, width - theme.PAD, 206))

        boxes = w.row(width, 226, height - w.PAD, 2)
        w.stat_card(draw, boxes[0],
                    "--" if env.temperature is None else f"{env.temperature:.1f}°",
                    "температура", value_size=30)
        w.stat_card(draw, boxes[1],
                    "--" if env.humidity is None else f"{env.humidity:.0f}%",
                    "влажность", value_size=30)

    def _verdict(self, co2: int) -> str:
        if co2 < 800:
            return "свежо"
        if co2 < 1400:
            return "пора проветрить"
        return "душно"

    def _scale(self, draw: ImageDraw.ImageDraw, co2: int, box: w.Box) -> None:
        """Полоса с зонами и отметкой — где показание относительно порогов."""
        x0, y0, x1, y1 = box
        span = x1 - x0
        zones = ((800, theme.OK), (1400, theme.WARN), (self.SCALE_MAX, theme.ALERT))
        left = x0
        for limit, color in zones:
            frac = (min(limit, self.SCALE_MAX) - self.SCALE_MIN) / (self.SCALE_MAX - self.SCALE_MIN)
            right = x0 + span * frac
            draw.rectangle((left, y0, right, y1), fill=tuple(c // 3 for c in color))
            left = right

        pos = (co2 - self.SCALE_MIN) / (self.SCALE_MAX - self.SCALE_MIN)
        mx = x0 + span * max(0.0, min(1.0, pos))
        draw.rectangle((mx - 2, y0 - 6, mx + 2, y1 + 6), fill=theme.FG)
        for value in (self.SCALE_MIN, 800, 1400, self.SCALE_MAX):
            frac = (value - self.SCALE_MIN) / (self.SCALE_MAX - self.SCALE_MIN)
            draw.text((x0 + span * frac, y1 + 16), str(value),
                      font=theme.font(11), fill=theme.DIM, anchor="mm")
