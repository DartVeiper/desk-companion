"""Поддельный ПК-агент: публикует ровно то, что будет слать настоящий.

    py tools/fake_agent.py                 # 60 секунд правдоподобной жизни
    py tools/fake_agent.py --host deskpi.local --seconds 300
    py tools/fake_agent.py --scenario game

Нужен, потому что настоящий агент написан на C# и до первой сборки от него
ничего не приходит, а проверить надо весь путь: контракт топиков, разбор
на плате, три экрана, которые без данных ПК пустуют, и строку состояния.

Полезен и после: чтобы воспроизвести редкое состояние (перегрев, AFK,
аномалия), настоящий ПК пришлось бы реально в него загонять.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time

ACTIVE_APP = "home/pc/active_app"
ACTIVITY = "home/pc/activity_1min"
AUDIO = "home/pc/audio"
HEARTBEAT = "home/pc/heartbeat"
HARDWARE = "home/pc/hardware"
ANOMALY = "home/pc/anomaly"

SCENARIOS = {
    "work": [("Code.exe", "code"), ("chrome.exe", "browser"), ("Code.exe", "code")],
    "game": [("dota2.exe", "game"), ("Discord.exe", "other"), ("dota2.exe", "game")],
    "idle": [("explorer.exe", "other")],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Поддельный ПК-агент")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="work")
    parser.add_argument("--afk", action="store_true", help="изображать отошедшего")
    parser.add_argument("--hot", action="store_true", help="изображать перегрев")
    args = parser.parse_args()

    import paho.mqtt.client as mqtt

    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="fake-agent")
    else:
        client = mqtt.Client(client_id="fake-agent")
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    def send(topic: str, payload, retain: bool = True) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        client.publish(topic, body, qos=0, retain=retain)
        print(f"  {topic:24} {body}")

    apps = SCENARIOS[args.scenario]
    print(f"\n  Поддельный агент -> {args.host}:{args.port}, "
          f"сценарий «{args.scenario}», {args.seconds:.0f} с\n")

    start = time.monotonic()
    tick = 0
    last_app = None
    while time.monotonic() - start < args.seconds:
        elapsed = time.monotonic() - start

        app, category = apps[int(elapsed // 20) % len(apps)]
        if (app, category) != last_app:
            last_app = (app, category)
            send(ACTIVE_APP, {"app": app, "category": category})

        if tick % 6 == 0:
            # Минутные агрегаты шлём чаще реальной минуты: ждать по минуте
            # ради проверки экрана — заведомо лишнее.
            send(ACTIVITY, {
                "keys": 0 if args.afk else random.randint(40, 320),
                "clicks": 0 if args.afk else random.randint(10, 90),
                "mouse_px": 0 if args.afk else random.randint(2000, 40000),
                "afk": args.afk,
            })
            send(AUDIO, "0" if args.afk else "1")

        if tick % 2 == 0:
            wave = (math.sin(elapsed / 7) + 1) / 2
            base = 78 if args.hot else 48
            send(HARDWARE, {
                "gpu_temp": round(base + wave * 14, 1),
                "gpu_load": round(20 + wave * 78, 1),
                "cpu_temp": round(base - 6 + wave * 12, 1),
                "cpu_load": round(10 + wave * 70, 1),
            })

        # Heartbeat без retain: сохранённая метка после выключения ПК врала
        # бы, что агент жив. Точно так же поступает настоящий агент.
        send(HEARTBEAT, json.dumps({"ts": int(time.time())}), retain=False)

        tick += 1
        time.sleep(5)

    client.loop_stop()
    client.disconnect()
    print("\n  готово\n")


if __name__ == "__main__":
    sys.exit(main())
