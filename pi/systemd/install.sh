#!/usr/bin/env bash
# Автозапуск сервисов Desk Companion.
#
#   sudo bash systemd/install.sh [--backup-to /mnt/backup]
#
# П.9 плана требует: после перезагрузки Pi всё поднимается само. Здесь это
# и настраивается — экран, дашборд и бэкап базы по расписанию.

set -euo pipefail

BACKUP_TO=""
[ "${1:-}" = "--backup-to" ] && BACKUP_TO="${2:-}"

[ "$(id -u)" -eq 0 ] || { echo "Запускай через sudo: sudo bash $0" >&2; exit 1; }

TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$(command -v python3)"
DB="/var/lib/desk-companion/desk.db"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()  { printf '    \033[1;32m[ok]\033[0m %s\n' "$*"; }

log "Каталог приложения: $APP_DIR (пользователь $TARGET_USER)"

# ---------------------------------------------------------------------------
# Сервис экрана
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/desk-companion.service <<EOF
[Unit]
Description=Desk Companion — экран и датчики
# Погода и NTP нужны сразу при старте, поэтому ждём именно сеть, а не просто
# загрузку: без адреса первый запрос всё равно упадёт.
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON -m app.main --db $DB
Restart=always
RestartSec=5
# Вывод в журнал, а не в файл: журнал уже ограничен 32 МБ на шаге 1, и
# отдельный лог-файл молча съел бы карту.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ok "desk-companion.service"

# ---------------------------------------------------------------------------
# Веб-дашборд
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/desk-dashboard.service <<EOF
[Unit]
Description=Desk Companion — веб-дашборд
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON dashboard_server.py --db $DB
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
ok "desk-dashboard.service"

# ---------------------------------------------------------------------------
# Бэкап по расписанию
# ---------------------------------------------------------------------------
if [ -n "$BACKUP_TO" ]; then
    cat > /etc/systemd/system/desk-backup.service <<EOF
[Unit]
Description=Desk Companion — резервная копия базы

[Service]
Type=oneshot
User=$TARGET_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON tools/backup.py --db $DB --to $BACKUP_TO
EOF

    cat > /etc/systemd/system/desk-backup.timer <<EOF
[Unit]
Description=Ежедневная копия базы Desk Companion

[Timer]
OnCalendar=daily
# Разброс, чтобы копия не бралась ровно в полночь одновременно со всем
# остальным, что запускается по расписанию.
RandomizedDelaySec=30m
# Пропущенный запуск (Pi был выключен) выполняется при следующем включении.
Persistent=true

[Install]
WantedBy=timers.target
EOF
    ok "desk-backup.timer -> $BACKUP_TO"
else
    echo "    бэкап не настроен: запусти с --backup-to /куда/класть"
    echo "    (п.6 плана: единственная копия на microSD — плохая идея)"
fi

# ---------------------------------------------------------------------------
log "Включение"
systemctl daemon-reload
systemctl enable --now desk-companion.service
systemctl enable --now desk-dashboard.service
[ -n "$BACKUP_TO" ] && systemctl enable --now desk-backup.timer

sleep 2
for unit in desk-companion desk-dashboard; do
    state=$(systemctl is-active "$unit" 2>/dev/null || echo "не запущен")
    printf '    %-20s %s\n' "$unit" "$state"
done

cat <<EOF

    Логи:        journalctl -u desk-companion -f
    Перезапуск:  sudo systemctl restart desk-companion
    Дашборд:     http://\$(hostname).local:843

    Проверка по п.9: перезагрузи Pi и убедись, что всё поднялось само.

EOF
