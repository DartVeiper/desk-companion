#!/usr/bin/env bash
# Доставка кода на Pi.
#
#   bash deploy.sh                    # туда же, куда в прошлый раз
#   bash deploy.sh alex@192.168.1.42  # новый адрес, он же и запомнится
#
# Через tar по ssh, а не scp: scp не умеет исключения, и на плату уезжали бы
# базы, кеши и сотня отрисованных превью — то есть лишние мегабайты на карту,
# которую мы и так бережём.
#
# Запускать из Git Bash (он идёт с git для Windows) — там есть и tar, и ssh.

set -euo pipefail

# Адрес запоминаем: из Windows deskpi.local резолвится через раз, и вбивать
# IP руками при каждой доставке — лишний повод ошибиться. Файл не в репозитории:
# адрес принадлежит конкретной сети, а не проекту.
HERE_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMEMBERED="$HERE_EARLY/.deploy-target"
if [ -n "${1:-}" ]; then
    TARGET="$1"
elif [ -s "$REMEMBERED" ]; then
    TARGET="$(cat "$REMEMBERED")"
else
    TARGET="deskpi@deskpi.local"
fi
REMOTE_DIR="${2:-desk-companion}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXCLUDES=(
    --exclude=__pycache__
    --exclude='*.pyc'
    --exclude='*.db'
    --exclude='*.db-wal'
    --exclude='*.db-shm'
    --exclude=preview        # отрисованные картинки нужны на машине разработки
    --exclude=.git
    --exclude=settings.json  # настройки принадлежат конкретному блоку
)

printf '\n\033[1;36m==> Доставка на %s:~/%s\033[0m\n' "$TARGET" "$REMOTE_DIR"

if ! ssh -o ConnectTimeout=8 -o BatchMode=no "$TARGET" true 2>/dev/null; then
    printf '\n  Не достучаться до %s\n' "$TARGET"
    printf '  Проверь, что Pi включён и в сети. Если deskpi.local не\n'
    printf '  резолвится — найди адрес в веб-интерфейсе роутера и передай его:\n'
    printf '      bash deploy.sh %s@192.168.1.42\n\n' "${TARGET%%@*}"
    exit 1
fi

printf '%s' "$TARGET" > "$REMEMBERED"

# Отпечаток доставляемого кода: его читает диагностика на самом блоке.
# Без него после каждой доставки непонятно, доехала ли она — а понимать
# это приходится часто, и гадание стоит дороже одной строки.
git -C "$HERE" describe --always --dirty 2>/dev/null > "$HERE/pi/.version" || true
date +" %d.%m %H:%M" >> "$HERE/pi/.version"
tr -d "
" < "$HERE/pi/.version" > "$HERE/pi/.version.tmp" && mv "$HERE/pi/.version.tmp" "$HERE/pi/.version"

SIZE=$(tar czf - -C "$HERE" "${EXCLUDES[@]}" pi | wc -c)
printf '    передаётся: %s КБ\n' "$((SIZE / 1024))"

ssh "$TARGET" "mkdir -p ~/$REMOTE_DIR"
tar czf - -C "$HERE" "${EXCLUDES[@]}" pi | ssh "$TARGET" "tar xzf - -C ~/$REMOTE_DIR --strip-components=1"

# Права на запуск после tar сохраняются, но если файлы приехали с Windows,
# бит запуска мог потеряться — выставляем явно.
ssh "$TARGET" "chmod +x ~/$REMOTE_DIR/*.sh ~/$REMOTE_DIR/systemd/*.sh 2>/dev/null || true"

REMOTE_FILES=$(ssh "$TARGET" "find ~/$REMOTE_DIR -name '*.py' | wc -l")
printf '\033[1;32m    готово: %s файлов .py на плате\033[0m\n' "$REMOTE_FILES"

cat <<EOF

  Дальше на самой плате:

      ssh $TARGET
      cd ~/$REMOTE_DIR
      sudo bash setup-step1.sh && sudo reboot     # если ещё не делал
      sudo bash setup-step2.sh                    # библиотеки приложения
      python3 tools/bringup.py kernel             # проверка интерфейсов

EOF
