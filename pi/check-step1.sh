#!/usr/bin/env bash
# Desk Companion — проверка шага 1. Запускать ПОСЛЕ перезагрузки:
#   sudo bash check-step1.sh

DB_PATH="${DB_PATH:-/var/lib/desk-companion/desk.db}"
PASS=0; FAIL=0

ok()   { printf '  \033[1;32mOK  \033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1;36m-- %s\033[0m\n' "$*"; }

head_ "Система"
printf '  %s\n' "$(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
printf '  kernel %s / %s\n' "$(uname -r)" "$(uname -m)"
printf '  hostname %s, uptime%s\n' "$(hostname)" "$(uptime -p | sed 's/^up//')"

head_ "Сеть и WiFi"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] && ok "IP: $IP" || bad "нет IP-адреса"
if command -v iwgetid >/dev/null 2>&1 && [ -n "$(iwgetid -r 2>/dev/null)" ]; then
    ok "WiFi SSID: $(iwgetid -r)  сигнал: $(awk 'NR==3{print $4}' /proc/net/wireless 2>/dev/null) dBm"
else
    printf '  --   SSID определить не удалось (не критично, если IP есть)\n'
fi
ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 && ok "интернет есть" || bad "интернета нет"
timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes \
    && ok "время синхронизировано по NTP ($(date '+%F %T %Z'))" \
    || bad "NTP не синхронизирован ($(date '+%F %T %Z'))"

head_ "I2C  (SCD41, шаг 5)"
[ -e /dev/i2c-1 ] && ok "/dev/i2c-1 есть" || bad "/dev/i2c-1 нет — I2C не включился"

head_ "SPI  (экран, шаг 2)"
[ -e /dev/spidev0.0 ] && ok "/dev/spidev0.0 есть" || bad "/dev/spidev0.0 нет — SPI не включился"

head_ "UART  (LD2410, шаг 4)"
if [ -e /dev/serial0 ]; then
    T=$(readlink -f /dev/serial0)
    if [ "$T" = "/dev/ttyAMA0" ]; then
        ok "/dev/serial0 -> ttyAMA0 (полноценный PL011, то что нужно)"
    else
        bad "/dev/serial0 -> $T (это mini-UART, скорость будет плыть — dtoverlay=disable-bt не применился)"
    fi
else
    bad "/dev/serial0 нет — enable_uart=1 не применился"
fi
grep -qE 'console=(serial0|ttyAMA0|ttyS0)' /boot/firmware/cmdline.txt /boot/cmdline.txt 2>/dev/null \
    && bad "в cmdline.txt осталась консоль на serial — порт занят системой" \
    || ok "последовательная консоль отключена"
systemctl is-active --quiet bluetooth 2>/dev/null \
    && bad "bluetooth.service ещё активен" \
    || ok "Bluetooth отключён"

head_ "MQTT  (mosquitto)"
if systemctl is-active --quiet mosquitto; then
    ok "служба запущена, автозапуск: $(systemctl is-enabled mosquitto 2>/dev/null)"
    OUT=$(mktemp)
    ( mosquitto_sub -h localhost -t desk/selftest -C 1 -W 5 > "$OUT" 2>/dev/null ) &
    SUB=$!
    sleep 1
    mosquitto_pub -h localhost -t desk/selftest -m pong >/dev/null 2>&1
    wait $SUB 2>/dev/null
    grep -q pong "$OUT" && ok "pub/sub round-trip работает" || bad "сообщение не дошло"
    rm -f "$OUT"
    ss -lntp 2>/dev/null | grep -q '0.0.0.0:1883' \
        && ok "слушает 1883 на всех интерфейсах (ПК-агент достучится)" \
        || bad "1883 не слушается снаружи — ПК-агент не подключится"
else
    bad "mosquitto не запущен"
fi

head_ "База данных"
if [ -f "$DB_PATH" ]; then
    TBL=$(sqlite3 "$DB_PATH" "SELECT group_concat(name,', ') FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';" 2>/dev/null)
    JM=$(sqlite3 "$DB_PATH" "PRAGMA journal_mode;" 2>/dev/null)
    case "$TBL" in
        *state_events*|*activity_minute*|*env_readings*) ok "$DB_PATH — таблицы: $TBL (journal_mode=$JM)" ;;
        *) bad "база есть, но таблиц нет" ;;
    esac
else
    bad "$DB_PATH не найдена"
fi

head_ "Ресурсы"
printf '  RAM   : %s\n' "$(free -h | awk 'NR==2{print $3" из "$2" занято"}')"
printf '  Диск  : %s\n' "$(df -h / | awk 'NR==2{print $3" из "$2" занято ("$5")"}')"
printf '  Темп. : %s\n' "$(vcgencmd measure_temp 2>/dev/null || echo '?')"
THR=$(vcgencmd get_throttled 2>/dev/null || echo '')
case "$THR" in
    throttled=0x0) printf '  Питание: норма\n' ;;
    "") ;;
    *) printf '  \033[1;33mПитание: %s — были просадки напряжения, проверь БП/кабель\033[0m\n' "$THR" ;;
esac

printf '\n\033[1m Итог: %d прошло, %d провалено\033[0m\n\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
