"""Тыкалка: экраны в браузере, без Pi и без железа.

    py preview_server.py          и открыть http://localhost:842

Показывает НАСТОЯЩИЙ кадр из настоящего кода отрисовки — не имитацию на
HTML. Если бы страница рисовала интерфейс своими средствами, получились бы
две реализации одного макета, и они разъехались бы на второй неделе.

Клик по картинке идёт через тот же GestureRecognizer, что будет работать с
резистивным тачем: клик — тап, протяжка мышью — свайп. То есть жесты
отлаживаются по-настоящему, а не «на словах».

Стандартная библиотека, ноль зависимостей: на Raspberry Pi OS Bookworm
установка пакетов мимо venv заблокирована, и лишний барьер тут ни к чему.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

PORT = 842
CONFIG = HERE / "app" / "config.toml"

# Значения, которые крутятся ползунками на странице.
FAKE: dict[str, object] = {
    "hour": 14, "minute": 32,
    "co2": 680, "temperature": 23.4, "humidity": 41,
    "out_temp": 18, "out_cond": "облачно", "rain": -1,
    "presence": True, "away_minutes": 0,
    "pc_online": True, "active_app": "rider64.exe", "category": "code",
    "keystrokes": 142, "clicks": 38, "audio": True, "afk": False,
    "gpu_temp": 68, "gpu_load": 92, "cpu_temp": 54, "cpu_load": 31,
    "anomaly": False, "anomaly_reason": "сессия 5 ч без перерыва",
    "streak": 4, "manual_status": "", "brightness": 80,
    "ambient": False, "ambient_index": 0, "held_ms": 0,
    # Здоровье блока: ползунки для строки состояния и диагностики
    "wifi_ok": True, "mqtt_ok": True, "scd41_ok": True, "ld2410_ok": True,
    "throttled": False, "disk_free_pct": 62.0, "pi_temp": 47.0,
}

_registry = None
_director = None
_bus = None
_gesture = None
_mtimes: dict[Path, float] = {}


def _source_files() -> list[Path]:
    return sorted((HERE / "app").rglob("*.py")) + [CONFIG]


def _changed() -> bool:
    global _mtimes
    current = {p: p.stat().st_mtime for p in _source_files() if p.exists()}
    if current != _mtimes:
        _mtimes = current
        return True
    return False


def reload_app() -> None:
    """Перечитать код экранов. Правишь файл — обновляешь страницу, и всё."""
    global _registry, _director, _bus, _gesture
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]

    from app import director as director_mod
    from app import theme
    from app.inputs.events import EventBus
    from app.inputs.gestures import GestureRecognizer
    from app.screens import registry as reg

    _registry = reg.load(CONFIG)
    _director = director_mod.load(CONFIG, _registry)
    _bus = EventBus()
    _gesture = GestureRecognizer(_bus, theme.WIDTH)


def build_state():
    from app.state import Env, Health, Pc, State, Weather

    now = datetime.now().replace(hour=int(FAKE["hour"]), minute=int(FAKE["minute"]),
                                 second=0, microsecond=0)
    rain = int(FAKE["rain"])
    state = State(
        now=now,
        env=Env(co2=int(FAKE["co2"]), temperature=float(FAKE["temperature"]),
                humidity=float(FAKE["humidity"]), updated=now),
        weather=Weather(temp=float(FAKE["out_temp"]), cond=str(FAKE["out_cond"]),
                        rain_soon_minutes=None if rain < 0 else rain),
        pc=Pc(
            active_app=str(FAKE["active_app"]), category=str(FAKE["category"]),
            keystrokes=int(FAKE["keystrokes"]), mouse_clicks=int(FAKE["clicks"]),
            audio_active=bool(FAKE["audio"]), afk=bool(FAKE["afk"]),
            gpu_temp=float(FAKE["gpu_temp"]), gpu_load=float(FAKE["gpu_load"]),
            cpu_temp=float(FAKE["cpu_temp"]), cpu_load=float(FAKE["cpu_load"]),
            anomaly_flag=bool(FAKE["anomaly"]), anomaly_reason=str(FAKE["anomaly_reason"]),
            last_heartbeat=now if FAKE["pc_online"] else None,
        ),
        health=Health(
            wifi_ok=bool(FAKE["wifi_ok"]),
            wifi_ssid="home-2.4" if FAKE["wifi_ok"] else "",
            wifi_signal_dbm=-54 if FAKE["wifi_ok"] else None,
            ip="192.168.1.42" if FAKE["wifi_ok"] else "",
            mqtt_ok=bool(FAKE["mqtt_ok"]),
            scd41_ok=bool(FAKE["scd41_ok"]),
            ld2410_ok=bool(FAKE["ld2410_ok"]),
            throttled=bool(FAKE["throttled"]),
            disk_free_pct=float(FAKE["disk_free_pct"]),
            cpu_temp=float(FAKE["pi_temp"]),
            uptime_seconds=int(3600 * 53.5),
        ),
        brightness=int(FAKE["brightness"]),
    )
    away = int(FAKE["away_minutes"])
    state.desk.presence = bool(FAKE["presence"])
    state.desk.presence_since = now - timedelta(minutes=away)
    state.desk.manual_status = FAKE["manual_status"] or None
    state.desk.streak_days = int(FAKE["streak"])
    return state


def sync_absence() -> None:
    """Подделать «человека нет уже N минут»: время тут стоит на ползунке,
    а ждать паузу по-настоящему в превью незачем.

    Вызывать только при смене ползунка, а не каждый кадр — иначе ввод не
    сможет разбудить экран: пробуждение сдвигает last_seen на сейчас, а
    следующий же кадр отматывал бы его обратно.
    """
    if _director is None or FAKE["presence"]:
        return
    state = build_state()
    _director.last_seen = state.now - timedelta(minutes=int(FAKE["away_minutes"]))


def current_screen(state):
    """Что показывать. Кнопки стилей покоя обходят Director намеренно:
    они «покажи вот этот стиль», а автопереход проверяется присутствием."""
    if FAKE["ambient"] and _director.ambient:
        return _director.ambient[int(FAKE["ambient_index"]) % len(_director.ambient)]
    return _director.current(state)


def render_frame() -> bytes:
    from PIL import Image, ImageDraw

    from app import theme
    from app.display.preview import PreviewDisplay

    state = build_state()
    screen = current_screen(state)

    from app.screens import widgets

    frame = Image.new("RGB", (theme.WIDTH, theme.HEIGHT), theme.BG)
    started = time.perf_counter()
    draw = ImageDraw.Draw(frame)
    screen.render(state, draw, frame)
    if int(FAKE["held_ms"]) > 0:
        widgets.hold_overlay(frame, float(FAKE["held_ms"]))
    took = (time.perf_counter() - started) * 1000

    display = PreviewDisplay()
    display.show(frame)
    render_frame.last_ms = took
    render_frame.last_title = screen.title
    return display.png_bytes()


render_frame.last_ms = 0.0
render_frame.last_title = ""


def drain_bus() -> None:
    state = build_state()
    while True:
        event = _bus.poll()
        if event is None:
            return
        if FAKE["ambient"]:
            FAKE["ambient"] = False  # ручной показ стиля снимается любым вводом
            continue
        _director.handle(event, state)
        # State пересобирается каждый кадр, поэтому всё, что экраны в нём
        # поменяли, надо забрать обратно в поддельные данные.
        FAKE["manual_status"] = state.desk.manual_status or ""
        FAKE["brightness"] = state.brightness


PAGE = """<!doctype html><meta charset=utf-8><title>Desk Companion — превью</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#111318;color:#e6e9f0;font:14px/1.45 system-ui,Segoe UI,sans-serif;
     display:flex;gap:24px;padding:24px;flex-wrap:wrap}
h2{font-size:15px;margin:22px 0 8px;color:#8b94a6;font-weight:600;letter-spacing:.02em}
h2:first-child{margin-top:0}
#stage{flex:0 0 auto}
#screen{width:960px;height:640px;image-rendering:pixelated;border-radius:10px;
        border:1px solid #2c323c;cursor:crosshair;display:block}
#meta{margin-top:10px;color:#7d8698;font-size:13px;display:flex;gap:16px}
#panel{flex:1 1 320px;max-width:420px}
.btns{display:flex;gap:8px;flex-wrap:wrap}
button{background:#1d222b;color:#e6e9f0;border:1px solid #333b47;border-radius:8px;
       padding:9px 14px;font:inherit;cursor:pointer}
button:hover{background:#28303c;border-color:#4a586c}
button.on{background:#1e3a5f;border-color:#3f7bc4}
button.bad{background:#4a1f22;border-color:#a04046;color:#f0b8bb}
.row{display:flex;align-items:center;gap:10px;margin:5px 0}
.row label{flex:0 0 118px;color:#8b94a6;font-size:13px}
.row input[type=range]{flex:1;accent-color:#5b9bf0}
.row input[type=text]{flex:1;background:#1a1f27;color:#e6e9f0;border:1px solid #333b47;
                      border-radius:6px;padding:5px 8px;font:inherit}
.row output{flex:0 0 62px;text-align:right;color:#e6e9f0;font-variant-numeric:tabular-nums}
</style>
<div id=stage>
  <img id=screen src="/frame.png">
  <div id=meta><span id=layer></span><span id=title></span><span id=ms></span>
    <span style="color:#5a6374">клик по экрану — тап, протяжка — свайп</span></div>
</div>
<div id=panel>
  <h2><a href="/" style="color:#5b9bf0">← дашборд</a></h2>
  <h2>Энкодер</h2>
  <div class=btns>
    <button onclick="act('prev')">← назад</button>
    <button onclick="act('next')">вперёд →</button>
    <button onclick="act('select')">нажать</button>
    <button onclick="act('hold')">держать 0,7 с</button>
    <button onclick="act('settings')">держать 3 с</button>
  </div>
  <h2>Экраны</h2>
  <div class=btns id=screens></div>
  <h2>Покой</h2>
  <div class=btns id=ambient></div>
  <h2>Отказы <span id=probs style="color:#7d8698;font-weight:400"></span></h2>
  <div class=btns id=faults></div>
  <h2>Данные</h2>
  <div id=fields></div>
  <h2>Код</h2>
  <div class=btns><button onclick="reloadCode()">перечитать код экранов</button></div>
</div>
<script>
const SLIDERS=[["hour","час",0,23,1],["minute","минута",0,59,1],
 ["co2","CO2",400,2500,10],["temperature","темп. в комнате",10,35,.1],
 ["humidity","влажность",10,90,1],["out_temp","темп. на улице",-30,40,1],
 ["rain","дождь через (-1 нет)",-1,60,1],["away_minutes","нет меня, мин",0,60,1],
 ["keystrokes","нажатий",0,600,1],["clicks","кликов",0,300,1],
 ["gpu_load","GPU загрузка",0,100,1],["gpu_temp","GPU темп.",30,95,1],
 ["cpu_load","CPU загрузка",0,100,1],["cpu_temp","CPU темп.",30,95,1],
 ["streak","стрик, дней",0,30,1],["brightness","яркость",10,100,5],
 ["disk_free_pct","место на карте, %",0,100,1],["pi_temp","температура Pi",30,90,1],
 ["held_ms","держат кнопку, мс",0,3600,50]];
const TOGGLES=[["presence","за столом"],["pc_online","ПК онлайн"],["audio","звук"],
 ["afk","AFK"],["anomaly","аномалия"]];
const FAULTS=[["wifi_ok","WiFi"],["mqtt_ok","MQTT"],["scd41_ok","SCD41"],
 ["ld2410_ok","LD2410"],["throttled","просадки питания"]];
const TEXTS=[["active_app","приложение"],["category","категория"],
 ["out_cond","погода"],["anomaly_reason","причина аномалии"]];

let S={};
async function pull(){S=await(await fetch('/state')).json();paint()}
function refresh(){document.getElementById('screen').src='/frame.png?'+Date.now()}
async function act(a){await fetch('/action?a='+a);await pull();refresh()}
async function goto_(n){await fetch('/goto?name='+encodeURIComponent(n));await pull();refresh()}
async function set(k,v){await fetch('/set?k='+k+'&v='+encodeURIComponent(v));await pull();refresh()}
async function reloadCode(){await fetch('/reload');await pull();refresh()}

function paint(){
  const L=document.getElementById('layer');
  L.textContent=S.layer;
  L.style.color=S.layer==='режимы'?'#7d8698':'#5b9bf0';
  document.getElementById('title').textContent=S.title;
  document.getElementById('ms').textContent=S.ms.toFixed(1)+' мс на кадр';
  document.getElementById('screens').innerHTML=S.screens.map((s,i)=>
    `<button class="${!S.fake.ambient&&i==S.index?'on':''}" onclick="goto_('${s.name}')">${i+1}. ${s.title}</button>`).join('');
  document.getElementById('ambient').innerHTML=S.ambient.map((s,i)=>
    `<button class="${S.fake.ambient&&i==S.fake.ambient_index?'on':''}"
      onclick="set('ambient_index',${i}).then(()=>set('ambient',1))">${s.title}</button>`).join('')
    +`<button class="${!S.fake.ambient?'on':''}" onclick="set('ambient',0)">выключить покой</button>`;

  // «Сломано» для throttled — это true, для остальных — false: они флаги «в норме».
  document.getElementById('faults').innerHTML=FAULTS.map(([k,l])=>{
    const broken = k==='throttled' ? S.fake[k] : !S.fake[k];
    return `<button class="${broken?'bad':''}" onclick="set('${k}',${S.fake[k]?0:1})">${l}</button>`;
  }).join(' ');
  document.getElementById('probs').textContent =
    S.problems.length ? '— ' + S.problems.join(', ') : '— всё в порядке';

  const f=document.getElementById('fields');
  if(f.dataset.built)return void f.querySelectorAll('input').forEach(el=>{
    if(el.type==='range'){el.value=S.fake[el.name];el.nextElementSibling.value=el.value}
    else if(el.type==='text'&&document.activeElement!==el)el.value=S.fake[el.name]});
  f.dataset.built=1;
  f.innerHTML=TOGGLES.map(([k,l])=>
      `<button class="${S.fake[k]?'on':''}" name="${k}" onclick="set('${k}',${S.fake[k]?0:1})">${l}</button>`).join(' ')
    +'<div style=height:10px></div>'
    +SLIDERS.map(([k,l,a,b,st])=>`<div class=row><label>${l}</label>
        <input type=range name="${k}" min=${a} max=${b} step=${st} value="${S.fake[k]}"
          oninput="this.nextElementSibling.value=this.value" onchange="set('${k}',this.value)">
        <output>${S.fake[k]}</output></div>`).join('')
    +TEXTS.map(([k,l])=>`<div class=row><label>${l}</label>
        <input type=text name="${k}" value="${S.fake[k]}" onchange="set('${k}',this.value)"></div>`).join('');
  f.querySelectorAll('button[name]').forEach(b=>b.className=S.fake[b.name]?'on':'');
}

const img=document.getElementById('screen');
let down=null;
function pos(e){const r=img.getBoundingClientRect();
  return [Math.round((e.clientX-r.left)/r.width*480),Math.round((e.clientY-r.top)/r.height*320)]}
img.addEventListener('mousedown',e=>{down=[...pos(e),Date.now()]});
img.addEventListener('mouseup',async e=>{
  if(!down)return; const [x,y]=pos(e); const [x0,y0,t0]=down; down=null;
  await fetch(`/tap?x0=${x0}&y0=${y0}&x1=${x}&y1=${y}&ms=${Date.now()-t0}`);
  await pull(); refresh();
});
pull();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # тише в консоли
        pass

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if _changed():
            reload_app()

        if url.path == "/preview":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")

        if url.path == "/frame.png":
            return self._send(render_frame(), "image/png")


        if url.path == "/reload":
            reload_app()

        elif url.path == "/action":
            from app.inputs.events import Action
            mapping = {"next": Action.NEXT, "prev": Action.PREV,
                       "select": Action.SELECT, "hold": Action.HOLD,
                       "settings": Action.SETTINGS}
            action = mapping.get(q.get("a", ""))
            if action:
                _bus.emit(action, "encoder")
                drain_bus()

        elif url.path == "/tap":
            _gesture.on_down(int(q["x0"]), int(q["y0"]))
            _gesture._t0 -= int(q.get("ms", 0)) / 1000.0  # длительность из браузера
            _gesture.on_move(int(q["x1"]), int(q["y1"]))
            _gesture.on_up()
            drain_bus()

        elif url.path == "/goto":
            FAKE["ambient"] = False
            _director.close_detail()
            _registry.go_to(q.get("name", ""))

        elif url.path == "/set":
            key, raw = q.get("k", ""), q.get("v", "")
            if key in FAKE:
                old = FAKE[key]
                if isinstance(old, bool):
                    FAKE[key] = raw not in ("0", "false", "")
                elif isinstance(old, int):
                    FAKE[key] = int(float(raw))
                elif isinstance(old, float):
                    FAKE[key] = float(raw)
                else:
                    FAKE[key] = raw
                if key in ("presence", "away_minutes", "hour", "minute"):
                    sync_absence()

        elif url.path != "/state":
            self.send_error(404)
            return

        # Заголовок берём от текущего экрана, а не от последнего отрисованного
        # кадра: страница спрашивает состояние чаще, чем картинку.
        current = current_screen(build_state())
        payload = {
            "screens": [{"name": s.name, "title": s.title} for s in _registry.screens],
            "ambient": [{"name": s.name, "title": s.title} for s in _director.ambient],
            "index": _registry.screens.index(_registry.current),
            "title": current.title,
            "layer": (" / ".join(_director.overlay_names) if _director.in_overlay
                      else "покой" if (_director.in_ambient or FAKE["ambient"])
                      else "режимы"),
            "problems": [p[0] for p in build_state().problems()],
            "away_delay": _director.away_delay.total_seconds() / 60,
            "ms": render_frame.last_ms,
            "fake": {k: v for k, v in FAKE.items()},
        }
        self._send(json.dumps(payload, ensure_ascii=False).encode(), "application/json")


def main() -> None:
    reload_app()
    _changed()
    print(f"\n  Desk Companion — превью экранов")
    print(f"  http://localhost:{PORT}\n")
    details = sum(len(s.details) for s in _registry.screens)
    print(f"  режимов: {len(_registry.screens)}, подробностей: {details}, "
          f"стилей покоя: {len(_director.ambient)}")
    print(f"  уход в покой через {_director.away_delay.total_seconds() / 60:.0f} мин без присутствия")
    print(f"  правь app/screens/*.py — страница подхватит сама\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
