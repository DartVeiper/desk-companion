"""Веб-дашборд истории — вариант А из п.7 плана, шаг 14.

    py dashboard_server.py --db preview/desk.db
    открыть http://localhost:843

Отдельно от preview_server.py сознательно: тот инструмент разработки и
показывает настоящий кадр экрана, а этот — продуктовая часть, которую
открывают с телефона в домашней сети. Общего у них только источник данных.

Стандартная библиотека, ноль зависимостей — на Raspberry Pi OS Bookworm
установка пакетов мимо venv заблокирована, и лишний барьер тут ни к чему.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from app import lang, settings, status  # noqa: E402
from app import touch_calibration  # noqa: E402
from app.db import Database  # noqa: E402
from app.screens.registry import load_config  # noqa: E402
from app.stats import WINDOW_HOURS, day_summary, streak_days, week_summary  # noqa: E402

CONFIG_PATH = HERE / "app" / "config.toml"

SITTING_LIMIT = WINDOW_HOURS * 60

PORT = 843
CATEGORY_ORDER = ("code", "browser", "game", "other")
CATEGORY_LABEL = {"code": "Код", "browser": "Браузер", "game": "Игры", "other": "Прочее"}

_db: Database | None = None


def api_today() -> dict:
    today = date.today()
    summary = day_summary(_db, today)
    start = datetime.combine(today, datetime.min.time())
    env = _db.env_between(start, start + timedelta(days=1))
    latest = env[-1] if env else None
    return {
        "date": today.isoformat(),
        "at_desk_minutes": summary.at_desk_minutes,
        "longest_sitting": summary.longest_sitting_minutes,
        "sitting_limit": SITTING_LIMIT,
        "keystrokes": summary.keystrokes,
        "clicks": summary.mouse_clicks,
        "streak": streak_days(_db, today),
        "hours": summary.by_hour,
        "by_category": {c: summary.by_category.get(c, 0) for c in CATEGORY_ORDER},
        "env_now": None if latest is None else {
            "co2": latest["co2"], "temperature": latest["temperature"],
            "humidity": latest["humidity"], "ts": latest["ts"],
        },
        "env_series": [{"ts": r["ts"], "co2": r["co2"], "temperature": r["temperature"]}
                       for r in env],
    }


def api_week() -> dict:
    week = week_summary(_db, date.today())
    return {
        "days": [{
            "date": s.day.isoformat(),
            "weekday": "пн вт ср чт пт сб вс".split()[s.day.weekday()],
            "at_desk_minutes": s.at_desk_minutes,
            "longest_sitting": s.longest_sitting_minutes,
            "by_category": {c: s.by_category.get(c, 0) for c in CATEGORY_ORDER},
        } for s in week.days],
        "total": week.total_minutes,
        "prev_total": week.previous_total_minutes,
        "sitting_limit": SITTING_LIMIT,
        "categories": [{"key": c, "label": CATEGORY_LABEL[c]} for c in CATEGORY_ORDER],
    }


def api_events(limit: int) -> dict:
    rows = _db.recent_events(limit)
    return {"events": [{
        "ts": r["ts"], "presence": bool(r["presence"]), "app": r["active_app"],
        "category": r["category"], "audio": bool(r["audio_active"]),
        "manual": r["manual_status"],
    } for r in rows]}


def api_live(language: str = "ru") -> dict:
    """Живое состояние из снимка, который пишет сервис.

    Отдельно от базы: туда идут агрегаты раз в минуту, а здоровье блока и
    энергия по зонам радара нужны «прямо сейчас» — в базу они не попадают
    вовсе.

    Текст для человека — погоду и неполадки — отдаём на языке, который
    попросил клиент. Снимок сервис пишет по-русски, а приложение на ПК может
    работать на другом языке, и без этого английское окно показывало бы
    русские подписи вперемешку со своими.
    """
    snapshot = status.read()
    if snapshot is None:
        return {"running": False,
                "hint": "сервис не запущен: py -m app.main --real"}
    snapshot["running"] = not snapshot["stale"]
    if snapshot["stale"]:
        snapshot["hint"] = f"снимок не обновлялся {snapshot['age_seconds']:.0f} с — сервис завис или остановлен"
    # Ворота LD2410 примерно по 0.75 м: подписи считаем здесь, чтобы
    # страница не знала про устройство радара.
    radar = snapshot.get("radar")
    if radar and radar.get("moving_gates"):
        radar["gate_labels"] = [f"{i * 75}–{(i + 1) * 75} см"
                                for i in range(len(radar["moving_gates"]))]
    weather = snapshot.get("weather")
    if weather and weather.get("cond"):
        weather["cond"] = lang.translate(weather["cond"], language)
    for problem in snapshot.get("problems", []):
        problem["label"] = lang.translate(problem["label"], language)
        problem["detail"] = lang.translate(problem["detail"], language)
    return snapshot


# Каталог режимов по п.10 плана. Порядок здесь — порядок в списке настроек,
# а не на устройстве: на устройстве порядок задаёт сам пользователь.
# Номеров в подписях нет намеренно: порядок задаётся перетаскиванием, и
# зашитая нумерация начала бы врать при первой же перестановке.
# Ручного статуса здесь нет — он накладка по удержанию, а не режим карусели.
SCREEN_CATALOG = (
    ("clock.ClockScreen", "Часы и погода"),
    ("details.WeatherDetail", "Погода подробно"),
    ("forecast.ForecastScreen", "Прогноз по частям суток"),
    ("details.AirDetail", "Воздух подробно"),
    ("activity.ActivityScreen", "Активность"),
    ("away.AwayScreen", "Меня нет"),
    ("hardware.HardwareScreen", "GPU / CPU"),
    ("anomaly.AnomalyScreen", "Аномалия"),
    ("streak.StreakScreen", "Стрик привычек"),
)
AMBIENT_CATALOG = (
    ("ambient.BigDigitsAmbient", "Крупные цифры"),
    ("ambient.MatrixAmbient", "Точечная матрица"),
    ("ambient.NightRedAmbient", "Ночной красный"),
)


def api_settings(language: str = "ru") -> dict:
    current = settings.load()
    config = settings.apply(load_config(CONFIG_PATH))
    enabled = list(config["screens"]["enabled"])
    labels = dict(SCREEN_CATALOG)
    # Сначала включённые — в том порядке, в каком их листает устройство;
    # следом выключенные. Список на странице и есть карусель, поэтому
    # порядок здесь обязан совпадать с настоящим.
    ordered = [k for k in enabled if k in labels]
    ordered += [k for k, _ in SCREEN_CATALOG if k not in enabled]
    return {
        "screens": [{"key": k, "label": lang.translate(labels[k], language),
                     "on": k in enabled}
                    for k in ordered],
        "ambient": [{"key": k, "label": lang.translate(label, language),
                     "on": k in config.get("ambient", {}).get("enabled", [])}
                    for k, label in AMBIENT_CATALOG],
        "values": {
            "away_delay_minutes": config["ambient"]["away_delay_minutes"],
            "night_from": config["ambient"]["night_from"],
            "night_to": config["ambient"]["night_to"],
            "brightness": current.get("display.brightness", 100),
            "co2_warn": current.get("air.co2_warn", 800),
            "co2_alert": current.get("air.co2_alert", 1400),
            # Умолчание берём из конфига, а не числом здесь: значение по
            # умолчанию, написанное в двух местах, однажды разойдётся, и
            # ползунок начнёт показывать не то, что стоит на самом деле.
            "touch_sensitivity": config["touch"]["max_resistance"],
            "language": config.get("ui", {}).get("language", "ru"),
            "city_name": config.get("location", {}).get("name", ""),
            "city_lat": config.get("location", {}).get("lat"),
            "city_lon": config.get("location", {}).get("lon"),
        },
    }


def save_settings(payload: dict) -> dict:
    values: dict = {}
    if isinstance(payload.get("screens"), list):
        # Хотя бы один режим должен остаться: пустая карусель — это чёрный
        # экран без способа что-либо вернуть с самого устройства.
        # Порядок берём из присланного списка, а не из каталога: иначе
        # перетаскивание на странице не значило бы ничего.
        known = {k for k, _ in SCREEN_CATALOG}
        chosen = [k for k in payload["screens"] if k in known]
        values["screens.enabled"] = chosen or [SCREEN_CATALOG[0][0]]
        # Запоминаем весь каталог, а не только включённое: иначе выключенный
        # экран не отличить от появившегося с обновлением, и один из них
        # обязательно будет обработан неверно.
        values["screens.known"] = [k for k, _ in SCREEN_CATALOG]
    if isinstance(payload.get("ambient"), list):
        values["ambient.enabled"] = [k for k, _ in AMBIENT_CATALOG if k in payload["ambient"]]
    for key, name in (("away_delay_minutes", "ambient.away_delay_minutes"),
                      ("night_from", "ambient.night_from"),
                      ("night_to", "ambient.night_to"),
                      ("brightness", "display.brightness"),
                      ("co2_warn", "air.co2_warn"),
                      ("co2_alert", "air.co2_alert"),
                      ("touch_sensitivity", "touch.max_resistance")):
        if key in payload:
            values[name] = payload[key]

    # Язык — только из известных. Неизвестный код означал бы экран, на
    # котором не найдено ни одной строки, то есть молча русский вид при
    # выбранном «эсперанто»; лучше не принять вовсе.
    if payload.get("language") in ("ru", "en"):
        values["ui.language"] = payload["language"]

    # Город — тройкой или никак. Разъехавшиеся название и координаты хуже
    # отсутствия города: на экране будет написан один, а погода показана
    # для другого, и понять это нельзя ничем.
    city = payload.get("city")
    if isinstance(city, dict) and {"name", "lat", "lon"} <= set(city):
        try:
            values["location.lat"] = float(city["lat"])
            values["location.lon"] = float(city["lon"])
            values["location.name"] = str(city["name"])
        except (TypeError, ValueError):
            pass

    settings.save(values)
    return api_settings()


#: Чем блок представляется приложению, которое ищет его в сети. Строка
#: нарочно своя, а не «ok»: на порту 843 в чужой сети может отвечать что
#: угодно, и принимать это за блок нельзя.
HELLO_APP = "desk-companion"


def api_hello() -> dict:
    """Визитка для поиска в сети.

    Приложение на ПК, не найдя блок по сохранённому адресу, обходит свою
    подсеть и спрашивает каждый адрес. Ответ должен быть мгновенным и
    дешёвым — без базы и без снимка с диска: адресов двести пятьдесят, и
    лишняя сотня миллисекунд на каждом сложилась бы в заметную паузу.
    """
    import socket

    return {"app": HELLO_APP, "name": socket.gethostname()}


def api_touch_calibration(language: str = "ru") -> dict:
    """На каком шаге калибровка тача. Причину неудачи — на языке клиента."""
    state = touch_calibration.read_state()
    if state.get("detail"):
        state["detail"] = lang.translate(state["detail"], language)
    return state


def request_touch_calibration(payload: dict, language: str = "ru") -> dict:
    """Попросить сервис начать или отменить калибровку.

    Сервис заметит просьбу за один проход цикла — это доли секунды. Ответ
    отдаём с состоянием «ждём сервис», а не с прежним: иначе приложение
    успело бы увидеть итог прошлой калибровки и принять его за нынешний.
    """
    import uuid

    action = payload.get("action")
    if action not in ("start", "cancel"):
        return {"state": "failed", "detail": "неизвестное действие"}
    request_id = uuid.uuid4().hex[:12]
    touch_calibration.write_request(action, request_id)
    state = api_touch_calibration(language)
    state.update({"id": request_id, "state": "requested"})
    return state


def api_health() -> dict:
    """Пока то, что видно из самой базы. Живые датчики приедут вместе с Pi."""
    first, last = _db.span()
    size = _db.size_bytes()
    stored = _db.stored_bytes()
    counts = {table: _db.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
              for table in ("activity_minute", "env_readings", "state_events")}
    days = max(1, (last - first).days + 1) if first else 1
    # Рост считаем по самой базе. Журнал WAL в неё не входит: он упирается
    # в потолок около четырёх мегабайт и переиспользуется, а не копится.
    # Пока дней мало, прикидка завышена — в размер входит разметка таблиц,
    # которая заводится один раз.
    daily = stored / days
    return {
        "db_path": str(_db.path),
        "db_size_kb": round(stored / 1024),
        "disk_kb": round(size / 1024),
        "rows": counts,
        "first_day": first.date().isoformat() if first else None,
        "last_day": last.date().isoformat() if last else None,
        # Прикидка роста нужна из-за п.6 плана: карта расходник, и полезно
        # заранее понимать, во что превратится база через год.
        "kb_per_day": round(daily / 1024, 1),
        "year_mb": round(daily * 365 / 1024 / 1024, 1),
        "days_counted": days,
    }


PAGE = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Desk Companion</title>
<style>
:root{
  color-scheme: light dark;
  --surface-1:#fcfcfb; --plane:#f9f9f7;
  --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme=light])){
  --surface-1:#1a1a19; --plane:#0d0d0d;
  --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
}}
:root[data-theme=dark]{
  --surface-1:#1a1a19; --plane:#0d0d0d;
  --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:6px;padding:16px 20px;flex-wrap:wrap}
h1{font-size:17px;margin:0 18px 0 0;font-weight:650}
nav button{background:none;border:0;color:var(--ink-2);font:inherit;cursor:pointer;
  padding:7px 12px;border-radius:8px}
nav button:hover{background:var(--ring)}
nav button[aria-current=page]{background:var(--surface-1);color:var(--ink);font-weight:600;
  box-shadow:0 0 0 1px var(--ring)}
#theme{margin-left:auto}
main{padding:0 20px 32px;max-width:1000px}
section{display:none}section.on{display:block}
.card{background:var(--surface-1);border-radius:14px;padding:18px 20px;margin-bottom:14px;
  box-shadow:0 0 0 1px var(--ring)}
.card h2{font-size:13px;margin:0 0 14px;color:var(--muted);font-weight:600;
  letter-spacing:.04em;text-transform:uppercase}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:12px}
.tile{background:var(--surface-1);border-radius:14px;padding:14px 16px;box-shadow:0 0 0 1px var(--ring)}
.tile .v{font-size:30px;font-weight:680;line-height:1.15}
.tile .k{font-size:12px;color:var(--muted);margin-top:2px}
.hours{display:flex;align-items:flex-end;gap:2px;height:130px}
.hours .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%}
.hours .bar{background:var(--s1);border-radius:4px 4px 0 0;min-height:2px}
.hlabels{display:flex;gap:2px;margin-top:6px}
.hlabels span{flex:1;text-align:center;font-size:10px;color:var(--muted);
  font-variant-numeric:tabular-nums}
.week{display:flex;align-items:flex-end;gap:10px;height:190px}
.week .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;gap:2px}
.week .seg{border-radius:2px;min-height:0}
.week .seg:first-child{border-radius:4px 4px 2px 2px}
.week .seg:last-child{border-radius:2px 2px 4px 4px}
.wlabels{display:flex;gap:10px;margin-top:8px}
.wlabels div{flex:1;text-align:center;font-size:12px}
.wlabels .d{color:var(--muted);font-size:11px}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px;font-size:13px;color:var(--ink-2)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;
  text-transform:uppercase;letter-spacing:.04em;padding:6px 8px}
td{padding:6px 8px;border-top:1px solid var(--grid);font-variant-numeric:tabular-nums}
td.n{text-align:right}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}
.bad{color:var(--crit);font-weight:600}
.ok{color:var(--good)}
.muted{color:var(--muted)}
.gates{display:grid;grid-template-columns:auto 1fr 1fr auto;gap:6px 12px;align-items:center}
.gates .lbl{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.gates .track{background:var(--grid);border-radius:4px;height:18px;overflow:hidden}
.gates .fill{height:100%;border-radius:4px;transition:width .15s linear}
.gates .num{font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums;
  text-align:right;min-width:56px}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px}
.order{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.ord{display:flex;align-items:center;gap:8px;border-radius:9px;background:var(--plane)}
.ord.drag{opacity:.4}
.ord.over{box-shadow:0 0 0 2px var(--s1)}
.ord .opt{flex:1;background:none}
.grip{padding:0 4px 0 10px;color:var(--muted);cursor:grab;font-size:15px;user-select:none}
.opt{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:9px;
  background:var(--plane);cursor:pointer}
.opt:hover{box-shadow:0 0 0 1px var(--ring)}
.opt input{accent-color:var(--s1);width:17px;height:17px}
.row{display:flex;align-items:center;gap:12px;margin:7px 0}
.row label{flex:0 0 150px;color:var(--ink-2);font-size:14px}
.row input[type=range]{flex:1;accent-color:var(--s1)}
.row output{flex:0 0 80px;text-align:right;font-variant-numeric:tabular-nums;font-size:13px}
.btnrow{display:flex;align-items:center;gap:14px;margin-bottom:14px}
.btnrow button{background:var(--s1);color:#fff;border:0;border-radius:9px;
  padding:10px 20px;font:inherit;font-weight:600;cursor:pointer}
.banner{background:var(--surface-1);border-radius:14px;padding:14px 18px;margin-bottom:14px;
  box-shadow:0 0 0 1px var(--ring);color:var(--ink-2)}
.banner b{color:var(--crit)}
#tip{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--ink);
  border-radius:8px;padding:7px 10px;font-size:12px;box-shadow:0 2px 12px rgba(0,0,0,.28),
  0 0 0 1px var(--ring);opacity:0;transition:opacity .1s;z-index:9}
svg{width:100%;height:150px;display:block;overflow:visible}
</style>

<header>
  <h1>Desk Companion</h1>
  <nav>
    <button data-tab=today aria-current=page>Сегодня</button>
    <button data-tab=week>Неделя</button>
    <button data-tab=events>События</button>
    <button data-tab=radar>Радар</button>
    <button data-tab=settings>Настройки</button>
    <button data-tab=health>База</button>
  </nav>
  <button id=theme title="Светлая / тёмная">◐</button>
</header>
<main>
  <section id=today class=on></section>
  <section id=week></section>
  <section id=events></section>
  <section id=radar></section>
  <section id=settings></section>
  <section id=health></section>
</main>
<div id=tip></div>

<script>
const SERIES = ['var(--s1)','var(--s2)','var(--s3)','var(--s4)'];
const tip = document.getElementById('tip');
const fmt = m => m >= 60 ? `${Math.floor(m/60)} ч ${String(m%60).padStart(2,'0')} мин` : `${m} мин`;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function hover(el, html){
  el.addEventListener('pointerenter', e => { tip.innerHTML = html; tip.style.opacity = 1; move(e); });
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerleave', () => tip.style.opacity = 0);
  function move(e){
    tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top  = Math.max(8, e.clientY - tip.offsetHeight - 12) + 'px';
  }
}

async function get(p){ return (await fetch(p)).json(); }

async function today(){
  const d = await get('/api/today');
  const peak = Math.max(1, ...d.hours);
  const over = d.longest_sitting > d.sitting_limit;
  const env = d.env_now;

  const tiles = [
    [fmt(d.at_desk_minutes), 'за столом сегодня', ''],
    [fmt(d.longest_sitting), `дольше всего без перерыва (предел ${d.sitting_limit} мин)`,
      over ? 'bad' : 'ok'],
    [d.streak, 'дней стрика', ''],
    [env ? env.co2 : '—', 'CO2, ppm', env && env.co2 > 1400 ? 'bad' : ''],
    [env ? env.temperature.toFixed(1) + '°' : '—', 'в комнате', ''],
    [d.keystrokes.toLocaleString('ru'), 'нажатий', ''],
  ];

  document.getElementById('today').innerHTML = `
    <div class=tiles>${tiles.map(([v,k,c]) =>
      `<div class=tile><div class="v ${c}">${v}</div><div class=k>${k}</div></div>`).join('')}</div>
    <div class=card><h2>За столом по часам</h2>
      <div class=hours id=hbars></div>
      <div class=hlabels>${d.hours.map((_,i)=>`<span>${i%3===0?i:''}</span>`).join('')}</div>
    </div>
    <div class=card><h2>CO2 за сутки</h2><div id=co2></div></div>`;

  const bars = document.getElementById('hbars');
  d.hours.forEach((m,h) => {
    const col = document.createElement('div'); col.className='col';
    const bar = document.createElement('div'); bar.className='bar';
    bar.style.height = (m/peak*100)+'%';
    if(!m) bar.style.background='var(--grid)';
    col.appendChild(bar); bars.appendChild(col);
    hover(col, `<b>${String(h).padStart(2,'0')}:00</b><br>${m} мин за столом`);
  });
  lineChart(document.getElementById('co2'), d.env_series);
}

function lineChart(host, series){
  if(!series.length){ host.innerHTML = '<p class=muted>данных за сегодня ещё нет</p>'; return; }
  const W=1000, H=150, P=24;
  const vals = series.map(p=>p.co2);
  const lo = Math.min(400, ...vals), hi = Math.max(1000, ...vals);
  const x = i => P + i*(W-2*P)/Math.max(1,series.length-1);
  const y = v => H-P - (v-lo)/(hi-lo)*(H-2*P);
  const path = series.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(p.co2).toFixed(1)}`).join('');
  const ticks = [800,1400].filter(t=>t>lo&&t<hi);
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio=none role=img
      aria-label="CO2 за сутки">
    ${ticks.map(t=>`<line x1=${P} x2=${W-P} y1=${y(t)} y2=${y(t)} stroke="var(--grid)"
        stroke-width=2 stroke-dasharray="4 6"/>
      <text x=${W-P} y=${y(t)-6} fill="var(--muted)" font-size=13 text-anchor=end>${t}</text>`).join('')}
    <line x1=${P} x2=${W-P} y1=${H-P} y2=${H-P} stroke="var(--axis)" stroke-width=2/>
    <path d="${path}" fill=none stroke="var(--s1)" stroke-width=2
      stroke-linejoin=round stroke-linecap=round/>
  </svg>`;
  const first = series[0].ts.slice(11,16), last = series.at(-1).ts.slice(11,16);
  hover(host, `<b>CO2 за сутки</b><br>${first} – ${last}<br>
    минимум ${Math.min(...vals)}, максимум ${Math.max(...vals)} ppm`);
}

async function week(){
  const d = await get('/api/week');
  const peak = Math.max(1, ...d.days.map(x=>x.at_desk_minutes));
  const delta = d.total - d.prev_total;
  const cols = d.days.map(day => {
    const segs = d.categories.map((c,i) => {
      const m = day.by_category[c.key] || 0;
      return m ? `<div class=seg style="height:${m/peak*100}%;background:${SERIES[i]}"
        data-t="${esc(c.label)}: ${m} мин"></div>` : '';
    }).join('');
    return `<div class=col data-day="${day.weekday}" data-total="${day.at_desk_minutes}"
      data-long="${day.longest_sitting}">${segs}</div>`;
  }).join('');

  document.getElementById('week').innerHTML = `
    <div class=tiles>
      <div class=tile><div class=v>${fmt(d.total)}</div><div class=k>за неделю</div></div>
      <div class=tile><div class="v ${delta>0?'bad':'ok'}">${delta>=0?'+':'−'}${fmt(Math.abs(delta))}</div>
        <div class=k>к прошлой неделе</div></div>
    </div>
    <div class=card><h2>По дням и категориям</h2>
      <div class=legend>${d.categories.map((c,i)=>
        `<span><i style="background:${SERIES[i]}"></i>${esc(c.label)}</span>`).join('')}</div>
      <div class=week id=wbars>${cols}</div>
      <div class=wlabels>${d.days.map(x=>
        `<div>${fmt(x.at_desk_minutes)}<div class=d>${x.weekday}</div></div>`).join('')}</div>
    </div>
    <div class=card><h2>Таблица</h2><table>
      <tr><th>день</th>${d.categories.map(c=>`<th class=n>${esc(c.label)}</th>`).join('')}
        <th class=n>всего</th><th class=n>сидка</th></tr>
      ${d.days.map(x=>`<tr><td>${x.weekday} ${x.date.slice(5)}</td>
        ${d.categories.map(c=>`<td class=n>${x.by_category[c.key]||0}</td>`).join('')}
        <td class=n><b>${x.at_desk_minutes}</b></td>
        <td class="n ${x.longest_sitting>d.sitting_limit?'bad':''}">${x.longest_sitting}</td></tr>`).join('')}
    </table></div>`;

  document.querySelectorAll('#wbars .col').forEach(col => {
    const parts = [...col.querySelectorAll('.seg')].map(s=>s.dataset.t).join('<br>');
    hover(col, `<b>${col.dataset.day}</b><br>${parts||'нет активности'}<br>
      всего ${col.dataset.total} мин · сидка ${col.dataset.long} мин`);
  });
}

async function events(){
  const d = await get('/api/events?limit=80');
  document.getElementById('events').innerHTML = `
    <div class=card><h2>Смены состояния</h2>
    ${d.events.length ? `<table>
      <tr><th>время</th><th>событие</th><th>приложение</th><th>звук</th></tr>
      ${d.events.map(e=>`<tr>
        <td class=muted>${e.ts.slice(5,16).replace(' ',' · ')}</td>
        <td><span class=dot style="background:${e.presence?'var(--good)':'var(--muted)'}"></span>
          ${e.presence?'пришёл':'ушёл'}</td>
        <td>${esc(e.app)||'<span class=muted>—</span>'}</td>
        <td class=muted>${e.audio?'да':'—'}</td></tr>`).join('')}
    </table>` : '<p class=muted>событий пока нет</p>'}</div>`;
}

function dur(seconds){
  const d = Math.floor(seconds/86400), h = Math.floor(seconds%86400/3600);
  return d ? `${d} д ${h} ч` : `${h} ч ${Math.floor(seconds%3600/60)} мин`;
}

async function health(){
  const [d, live] = await Promise.all([get('/api/health'), get('/api/live')]);
  const h = live.running ? live.health : null;
  const problems = live.running ? live.problems : [];

  const banner = live.running
    ? (problems.length
        ? `<div class=banner><b>Неисправности:</b> ${problems.map(p=>esc(p.detail)).join('; ')}</div>`
        : '')
    : `<div class=banner>Сервис не запущен — живых данных нет. ${esc(live.hint||'')}</div>`;

  const rows = h ? [
    ['WiFi', h.wifi_ssid || 'нет сети', h.wifi_ok],
    ['адрес', h.ip || '—', !!h.ip],
    ['MQTT', h.mqtt_ok ? 'работает' : 'не отвечает', h.mqtt_ok],
    ['SCD41', h.scd41_ok ? 'отвечает' : 'молчит', h.scd41_ok],
    ['LD2410', h.ld2410_ok ? 'отвечает' : 'молчит', h.ld2410_ok],
    ['питание', h.throttled ? 'просадки' : 'норма', !h.throttled],
    ['температура Pi', h.cpu_temp==null ? '—' : h.cpu_temp+' °C', h.cpu_temp==null||h.cpu_temp<75],
    ['свободно на карте', h.disk_free_pct+' %', h.disk_free_pct>=10],
    ['аптайм', dur(h.uptime_seconds), true],
    ['ПК-агент', live.pc_online ? 'онлайн' : 'офлайн', true],
  ] : [];

  document.getElementById('health').innerHTML = banner + `
    ${h ? `<div class=card><h2>Живое состояние блока</h2><table>
      ${rows.map(([k,v,ok])=>`<tr>
        <td><span class=dot style="background:${ok?'var(--good)':'var(--crit)'}"></span>${esc(k)}</td>
        <td class="n ${ok?'':'bad'}">${esc(v)}</td></tr>`).join('')}
    </table></div>` : ''}
    <div class=tiles>
      <div class=tile><div class=v>${d.db_size_kb} КБ</div><div class=k>размер базы</div></div>
      <div class=tile><div class=v>${d.kb_per_day} КБ</div><div class=k>прирост в сутки</div></div>
      <div class=tile><div class=v>${d.year_mb} МБ</div><div class=k>прогноз за год</div></div>
    </div>
    <div class=card><h2>Таблицы</h2><table>
      <tr><th>таблица</th><th class=n>строк</th></tr>
      ${Object.entries(d.rows).map(([t,c])=>
        `<tr><td>${t}</td><td class=n>${c.toLocaleString('ru')}</td></tr>`).join('')}
    </table></div>
    <div class=card><h2>Файл</h2>
      <p class=muted>${esc(d.db_path)}<br>данные с ${d.first_day||'—'} по ${d.last_day||'—'}</p>
      <p><a href="/db" download>Скачать базу</a> — держать единственную копию
      на microSD рискованно: карта в этом проекте расходник.</p></div>`;
}

let liveTimer = null;

async function radar(){
  const d = await get('/api/live');
  const host = document.getElementById('radar');

  if(!d.running){
    host.innerHTML = `<div class=banner><b>Нет живых данных.</b> ${esc(d.hint||'')}</div>`;
    return;
  }
  const r = d.radar;
  if(!r){
    host.innerHTML = `<div class=banner>Сервис работает, но радар не отвечает.
      Проверь подключение: <code>python3 tools/bringup.py radar</code></div>`;
    return;
  }

  const gates = (r.moving_gates||[]).map((mv,i) => {
    const st = (r.static_gates||[])[i] ?? 0;
    return `<div class=lbl>${esc((r.gate_labels||[])[i]||i)}</div>
      <div class=track><div class=fill style="width:${mv}%;background:var(--s1)"></div></div>
      <div class=track><div class=fill style="width:${st}%;background:var(--s2)"></div></div>
      <div class=num>${mv} / ${st}</div>`;
  }).join('');

  host.innerHTML = `
    <div class=tiles>
      <div class="tile"><div class="v ${r.present?'ok':''}">${esc(r.state)}</div>
        <div class=k>состояние</div></div>
      <div class=tile><div class=v>${r.distance_cm} см</div><div class=k>дистанция</div></div>
      <div class=tile><div class=v>${r.moving_energy}</div><div class=k>энергия движения</div></div>
      <div class=tile><div class=v>${r.static_energy}</div><div class=k>энергия статики</div></div>
    </div>
    <div class=card><h2>Энергия по зонам дальности</h2>
      <div class=legend>
        <span><i style="background:var(--s1)"></i>движение</span>
        <span><i style="background:var(--s2)"></i>статика</span>
      </div>
      ${gates ? `<div class=gates>${gates}</div>`
              : '<p class=muted>инженерный режим выключен — включи engineering в [radar]</p>'}
    </div>
    <div class=card><h2>Как калибровать</h2>
      <p class=muted>Покачай <b>пустое</b> кресло: если какая-то зона отзывается,
      порог в ней надо поднять. Посидев неподвижно, смотри на статику — она
      должна держаться. Выйди из зоны: всё должно упасть.<br>
      Пороги задаются командой <code>set_gate_sensitivity</code> из
      <code>app/drivers/ld2410.py</code>.</p></div>`;
}

async function settingsTab(){
  const d = await get('/api/settings');
  const v = d.values;
  const rowBox = item =>
    `<li class=ord draggable=true data-key="${item.key}">
       <span class=grip>⠿</span>
       <label class=opt><input type=checkbox data-group=screens value="${item.key}"
         ${item.on?'checked':''}> ${esc(item.label)}</label></li>`;
  const box = (item, group) =>
    `<label class=opt><input type=checkbox data-group=${group} value="${item.key}"
      ${item.on?'checked':''}> ${esc(item.label)}</label>`;
  // Перетаскивание порядка. Своими руками, без библиотеки: правил тут
  // немного, а лишняя зависимость в странице, которую отдаёт сам блок,
  // означала бы либо интернет при загрузке, либо копию файла на карте.
  const wireDrag = () => {
    const list = document.getElementById('order');
    if (!list) return;
    let dragged = null;
    list.querySelectorAll('.ord').forEach(row => {
      row.addEventListener('dragstart', e => {
        dragged = row; row.classList.add('drag');
        e.dataTransfer.effectAllowed = 'move';
      });
      row.addEventListener('dragend', () => {
        row.classList.remove('drag');
        list.querySelectorAll('.ord').forEach(r => r.classList.remove('over'));
      });
      row.addEventListener('dragover', e => {
        e.preventDefault();
        if (row !== dragged) row.classList.add('over');
      });
      row.addEventListener('dragleave', () => row.classList.remove('over'));
      row.addEventListener('drop', e => {
        e.preventDefault();
        row.classList.remove('over');
        if (!dragged || row === dragged) return;
        const rows = [...list.querySelectorAll('.ord')];
        const before = rows.indexOf(dragged) < rows.indexOf(row);
        row.parentNode.insertBefore(dragged, before ? row.nextSibling : row);
      });
    });
  };

  const num = (key, label, min, max, step, unit) =>
    `<div class=row><label>${label}</label>
      <input type=range name="${key}" min=${min} max=${max} step=${step} value="${v[key]}"
        oninput="this.nextElementSibling.value=this.value+'${unit}'">
      <output>${v[key]}${unit}</output></div>`;

  document.getElementById('settings').innerHTML = `
    <div class=card><h2>Какие режимы листать</h2>
      <ol class=order id=order>${d.screens.map(s=>rowBox(s)).join('')}</ol>
      <p class=muted>Перетаскивай за ручку — так и будет листаться крутилкой.
      Снять все нельзя: пустая карусель это чёрный экран, с которого уже
      ничего не вернуть.</p></div>

    <div class=card><h2>Стили экрана покоя</h2>
      <div class=opts>${d.ambient.map(s=>box(s,'ambient')).join('')}</div>
      <p class=muted>Включается, когда радар не видит присутствия. Стиль
      выбирается новый при каждом входе, ночью — принудительно красный.</p></div>

    <div class=card><h2>Пороги</h2>
      ${num('away_delay_minutes','уход в покой',2,15,1,' мин')}
      ${num('night_from','ночь с',18,23,1,' ч')}
      ${num('night_to','ночь до',4,10,1,' ч')}
      ${num('brightness','яркость экрана',10,100,5,' %')}
      ${num('co2_warn','CO2: жёлтый от',600,1200,50,' ppm')}
      ${num('co2_alert','CO2: красный от',1000,2000,50,' ppm')}
      <p class=muted>Меньше двух минут на уход в покой ставить не стоит: радар
      периодически теряет неподвижного человека, и экран начнёт дёргаться.</p></div>

    <div class=card><h2>Язык надписей на блоке</h2>
      <div class=row><label>язык</label>
        <select name="language" id="language">
          <option value="ru"${v.language === 'en' ? '' : ' selected'}>Русский</option>
          <option value="en"${v.language === 'en' ? ' selected' : ''}>English</option>
        </select></div>
      <p class=muted>Меняет только надписи на экране блока. Названия окон,
      треков и сетей остаются как есть — это чужой текст, а не наш
      интерфейс.</p></div>

    <div class=card><h2>Чувствительность экрана</h2>
      ${num('touch_sensitivity','нажатие',2000,15000,500,'')}
      <p class=muted>Вправо — легче нажимать. Панель резистивная: она меряет
      не касание, а насколько сильно прижались друг к другу два слоя, и
      ползунок задаёт, с какого прижатия считать это нажатием.</p>
      <p class=muted>Поднимать почти безопасно — у нетронутой панели ложных
      срабатываний не возникает ни при каком пороге. Если всё же начало
      нажиматься само, ведите влево.</p></div>

    <div class=btnrow><button id=save>Применить</button>
      <span id=saved class=muted></span></div>`;

  // Порядок в запросе берётся из порядка элементов на странице, а его
  // меняет перетаскивание — отдельного поля не нужно.
  wireDrag();

  document.getElementById('save').onclick = async () => {
    const pick = g => [...document.querySelectorAll(`input[data-group=${g}]:checked`)]
      .map(i => i.value);
    const payload = {screens: pick('screens'), ambient: pick('ambient')};
    document.querySelectorAll('#settings input[type=range]')
      .forEach(i => payload[i.name] = +i.value);
    payload.language = document.getElementById('language').value;
    await fetch('/api/settings', {method:'POST', body: JSON.stringify(payload)});
    // Сервис сам заметит правку файла и пересоберёт экраны — перезапускать
    // его не нужно.
    document.getElementById('saved').textContent =
      'сохранено, устройство подхватит в течение секунды';
    setTimeout(settingsTab, 1200);
  };
}

const TABS = {today, week, events, health, radar, settings: settingsTab};
document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x=>x.removeAttribute('aria-current'));
  b.setAttribute('aria-current','page');
  document.querySelectorAll('section').forEach(s=>s.classList.remove('on'));
  document.getElementById(b.dataset.tab).classList.add('on');
  TABS[b.dataset.tab]();
  // Опрашиваем только пока открыта вкладка, которой это нужно: калибровать
  // радар без живой картинки бессмысленно, а на остальных вкладках лишний
  // запрос дважды в секунду ни к чему.
  clearInterval(liveTimer);
  liveTimer = null;
  if(b.dataset.tab === 'radar') liveTimer = setInterval(radar, 500);
  if(b.dataset.tab === 'health') liveTimer = setInterval(health, 3000);
});
document.getElementById('theme').onclick = () => {
  const dark = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
};
today();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _fail(self, code: int, text: str) -> None:
        """Отказ с объяснением — телом ответа, а не строкой статуса.

        send_error кладёт текст в строку статуса, а её http.server кодирует
        в latin-1. Кириллица там роняла обработчик, и вместо «400, битый
        JSON» клиент получал оборванное соединение без единого слова.
        """
        body = json.dumps({"error": text}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        handlers = {
            "/api/settings": lambda body: save_settings(body),
            "/api/touch-calibration":
                lambda body: request_touch_calibration(body, q.get("lang", "ru")),
        }
        handler = handlers.get(url.path)
        if handler is None:
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._fail(400, "битый JSON")
        if not isinstance(payload, dict):
            return self._fail(400, "ждём JSON-объект")
        # Кривой запрос не должен рвать соединение без ответа. Так и было:
        # приложение слало экраны не в том виде, обработчик падал, и клиент
        # видел только оборванное соединение — без единого слова о причине.
        try:
            result = handler(payload)
        except (TypeError, ValueError, KeyError) as error:
            return self._fail(400, f"не разобрать запрос: {error}")
        self._send(json.dumps(result, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        routes = {
            "/api/today": api_today,
            "/api/week": api_week,
            "/api/health": api_health,
            "/api/live": lambda: api_live(q.get("lang", "ru")),
            "/api/settings": lambda: api_settings(q.get("lang", "ru")),
            "/api/touch-calibration": lambda: api_touch_calibration(q.get("lang", "ru")),
            "/api/hello": api_hello,
            "/api/events": lambda: api_events(int(q.get("limit", 50))),
        }

        if url.path == "/":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")

        if url.path == "/db":
            # Бэкап одной кнопкой (задача из п.6/шага 12). Копируем через
            # SQLite backup, а не файлом: при включённом WAL простое
            # копирование может поймать базу в середине транзакции.
            # Через backup API, а не чтением файла: при включённом WAL
            # копирование может поймать базу в середине транзакции.
            from tools.backup import make_backup
            tmp_dir = _db.path.parent / ".download"
            copy = make_backup(_db.path, tmp_dir)
            body = copy.read_bytes()
            copy.unlink(missing_ok=True)
            tmp_dir.rmdir()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             f'attachment; filename="desk-{date.today().isoformat()}.db"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        handler = routes.get(url.path)
        if handler is None:
            return self.send_error(404)
        self._send(json.dumps(handler(), ensure_ascii=False).encode(), "application/json")


def main() -> None:
    global _db
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="preview/desk.db")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    _db = Database(HERE / args.db if not Path(args.db).is_absolute() else args.db)
    if not _db.path.exists():
        print(f"\n  базы нет: {_db.path}")
        print("  заполни тестовыми данными:  py tools/seed.py --days 21\n")
        raise SystemExit(1)

    first, last = _db.span()
    print("\n  Desk Companion — дашборд")
    print(f"  http://localhost:{args.port}\n")
    print(f"  база : {_db.path} ({_db.size_bytes() / 1024:.0f} КБ)")
    print("  дни  : " + (f"{first.date()} .. {last.date()}" if first else "данных нет"))
    print(f"  стрик: {streak_days(_db)}\n")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
