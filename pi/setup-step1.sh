#!/usr/bin/env bash
# Desk Companion — шаг 1: базовая настройка Raspberry Pi Zero 2 W
#
#   sudo bash setup-step1.sh              # настройка
#   sudo bash setup-step1.sh --upgrade    # + полное обновление системы (долго, 20-40 мин)
#
# Скрипт идемпотентный: можно запускать повторно, ничего не сломается.
# Что делает:
#   1. Включает I2C, SPI, аппаратный UART на GPIO14/15
#   2. Отключает Bluetooth (освобождает нормальный PL011 UART под LD2410)
#   3. Убирает системную консоль с последовательного порта
#   4. Ставит mosquitto + sqlite3 + i2c-tools
#   5. Настраивает брокер на приём из локальной сети, с persistence под retain
#   6. Создаёт пустую базу по схеме из п.4 плана
#   7. Ограничивает журнал systemd (бережём microSD)

set -euo pipefail

DB_DIR="${DB_DIR:-/var/lib/desk-companion}"
DB_PATH="$DB_DIR/desk.db"
MARK_BEGIN="# >>> desk-companion step1 >>>"
MARK_END="# <<< desk-companion step1 <<<"
DO_UPGRADE=0
[ "${1:-}" = "--upgrade" ] && DO_UPGRADE=1

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '    \033[1;33m[!]\033[0m  %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Нужны права root. Запускай: sudo bash $0" >&2
    exit 1
fi

TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"

# ---------------------------------------------------------------------------
# 0. Где лежит boot-конфиг: Bookworm и новее — /boot/firmware, старее — /boot
# ---------------------------------------------------------------------------
if   [ -f /boot/firmware/config.txt ]; then BOOT=/boot/firmware
elif [ -f /boot/config.txt ];          then BOOT=/boot
else echo "Не найден config.txt ни в /boot/firmware, ни в /boot" >&2; exit 1
fi

log "Boot-конфиг: $BOOT/config.txt"
[ -f "$BOOT/config.txt.dc-bak"  ] || cp "$BOOT/config.txt"  "$BOOT/config.txt.dc-bak"
[ -f "$BOOT/cmdline.txt.dc-bak" ] || cp "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.dc-bak"
ok "бэкапы: config.txt.dc-bak / cmdline.txt.dc-bak"

# ---------------------------------------------------------------------------
# 1. Интерфейсы: I2C + SPI + UART, Bluetooth — долой
# ---------------------------------------------------------------------------
log "Интерфейсы: I2C, SPI, UART, отключение Bluetooth"

# Гасим конфликтующие строки выше по файлу, чтобы наш блок точно выиграл.
# Разделитель @, а не |: внутри шаблона | уже занят под «или», и sed оборвал
# бы выражение на нём, не дойдя до закрывающей скобки.
sed -i -E 's@^[[:space:]]*(dtparam=(i2c_arm|spi)=off)@# \1  # выключено desk-companion@' "$BOOT/config.txt"

# Вырезаем предыдущий наш блок (для повторного запуска)
sed -i "\%^${MARK_BEGIN}\$%,\%^${MARK_END}\$%d" "$BOOT/config.txt"

# [all] сбрасывает возможный секционный фильтр выше по файлу ([pi4], [cm4]...)
cat >> "$BOOT/config.txt" <<EOF
${MARK_BEGIN}
[all]
dtparam=i2c_arm=on
dtparam=spi=on
enable_uart=1
dtoverlay=disable-bt
${MARK_END}
EOF
ok "config.txt: i2c_arm=on, spi=on, enable_uart=1, dtoverlay=disable-bt"

grep -qxF 'i2c-dev' /etc/modules 2>/dev/null || echo 'i2c-dev' >> /etc/modules
ok "i2c-dev в /etc/modules"

# Консоль ядра на serial мешает LD2410 — убираем (cmdline.txt должен остаться в одну строку)
sed -i -E 's/[[:space:]]*console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' "$BOOT/cmdline.txt"
ok "cmdline.txt: последовательная консоль убрана"

for svc in hciuart.service bluetooth.service serial-getty@ttyAMA0.service serial-getty@ttyS0.service; do
    systemctl disable --now "$svc" >/dev/null 2>&1 && ok "отключён $svc" || true
done

for g in dialout i2c spi gpio; do
    getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$TARGET_USER" || true
done
ok "пользователь $TARGET_USER добавлен в dialout/i2c/spi/gpio"

# ---------------------------------------------------------------------------
# 2. Пакеты
# ---------------------------------------------------------------------------
log "Установка пакетов"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
if [ "$DO_UPGRADE" -eq 1 ]; then
    log "Полное обновление системы (это надолго)"
    apt-get full-upgrade -y
fi
apt-get install -y --no-install-recommends \
    mosquitto mosquitto-clients \
    sqlite3 i2c-tools git \
    python3-venv python3-pip
ok "mosquitto, sqlite3, i2c-tools, git, python3-venv"

# ---------------------------------------------------------------------------
# 3. Mosquitto
# ---------------------------------------------------------------------------
log "Настройка MQTT-брокера"
install -d -m 0755 /etc/mosquitto/conf.d
cat > /etc/mosquitto/conf.d/desk-companion.conf <<'EOF'
# Desk Companion — локальный брокер.
# Слушаем всю локальную сеть: ПК-агент (п.6 плана) публикует с игрового ПК.
listener 1883 0.0.0.0

# ВНИМАНИЕ: пока без пароля — брокер открыт всем в домашней сети.
# Перед шагом 6 (ПК-агент) закрыть: mosquitto_passwd + allow_anonymous false.
allow_anonymous true

max_queued_messages 200
EOF

# persistence нужен, чтобы retain-сообщения (п.5 плана) пережили перезагрузку.
# Пакет Debian включает его в основном конфиге сам, а mosquitto 2.x считает
# повторное объявление директивы не уточнением, а фатальной ошибкой: брокер
# не стартует вовсе. Поэтому дописываем только то, чего там нет.
if ! grep -qE '^[[:space:]]*persistence_location' /etc/mosquitto/mosquitto.conf 2>/dev/null; then
    printf 'persistence true\npersistence_location /var/lib/mosquitto/\n' \
        >> /etc/mosquitto/conf.d/desk-companion.conf
    ok "persistence включён нами"
else
    ok "persistence уже включён пакетом, не дублируем"
fi
if ! grep -qE '^[[:space:]]*autosave_interval' /etc/mosquitto/mosquitto.conf 2>/dev/null; then
    printf 'autosave_interval 300\n' >> /etc/mosquitto/conf.d/desk-companion.conf
fi

systemctl enable mosquitto >/dev/null 2>&1 || true
# Без этого «Start request repeated too quickly» после неудачных попыток
# переживает даже исправленный конфиг: systemd держит счётчик рестартов.
systemctl reset-failed mosquitto >/dev/null 2>&1 || true
systemctl restart mosquitto
ok "mosquitto слушает 1883 на всех интерфейсах, автозапуск включён"

# ---------------------------------------------------------------------------
# 4. SQLite — пустая база по схеме из п.4 плана
# ---------------------------------------------------------------------------
log "База данных"
install -d -m 0755 -o "$TARGET_USER" -g "$TARGET_USER" "$DB_DIR"

cat > "$DB_DIR/schema.sql" <<'EOF'
-- Desk Companion — схема из п.4 плана.
-- WAL: меньше записей на microSD + дашборд может читать во время записи.
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- События смены состояния (мало записей, только на изменение)
CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY,
    ts DATETIME,
    presence BOOLEAN,
    active_app TEXT,
    category TEXT,          -- code / game / browser / other
    audio_active BOOLEAN,
    manual_status TEXT      -- NULL если не задан вручную
);
CREATE INDEX IF NOT EXISTS idx_state_events_ts ON state_events(ts);

-- Поминутные агрегаты активности (компактно, ~1440 строк/день)
CREATE TABLE IF NOT EXISTS activity_minute (
    ts DATETIME PRIMARY KEY,
    keystrokes INTEGER,
    mouse_clicks INTEGER,
    mouse_distance_px INTEGER,
    at_desk BOOLEAN,
    category TEXT
);

-- Показания воздуха (раз в 5-10 мин достаточно, не каждую секунду)
CREATE TABLE IF NOT EXISTS env_readings (
    ts DATETIME PRIMARY KEY,
    co2 INTEGER,
    temperature REAL,
    humidity REAL
);
EOF

sqlite3 "$DB_PATH" < "$DB_DIR/schema.sql"
chown "$TARGET_USER":"$TARGET_USER" "$DB_PATH" "$DB_DIR/schema.sql"
chmod 0664 "$DB_PATH"
ok "$DB_PATH — таблицы: $(sqlite3 "$DB_PATH" "SELECT group_concat(name,', ') FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")"

# ---------------------------------------------------------------------------
# 5. Бережём microSD: ограничиваем журнал
# ---------------------------------------------------------------------------
log "Ограничение журнала systemd"
install -d -m 0755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/desk-companion.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=32M
RuntimeMaxUse=16M
EOF
systemctl restart systemd-journald || true
ok "журнал ограничен 32 МБ"

# ---------------------------------------------------------------------------
# Итог
# ---------------------------------------------------------------------------
log "Готово"
printf '    Часовой пояс : %s\n' "$(timedatectl show -p Timezone --value 2>/dev/null || echo '?')"
printf '    IP-адрес     : %s\n' "$(hostname -I 2>/dev/null | tr -s ' ' || echo '?')"
printf '    База         : %s\n' "$DB_PATH"
echo
warn "Нужна ПЕРЕЗАГРУЗКА — правки config.txt/cmdline.txt применяются только при старте:"
echo "        sudo reboot"
echo
echo "    После перезагрузки прогони проверку:  sudo bash check-step1.sh"
