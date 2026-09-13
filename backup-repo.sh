#!/usr/bin/env bash
# Копия всей истории проекта на другое устройство.
#
#   bash backup-repo.sh                       # на плату, alex@192.168.1.205
#   bash backup-repo.sh alex@192.168.1.42     # другой адрес
#   bash backup-repo.sh --to D:/backup        # или просто в папку
#
# Зачем, если есть git. Git защищает от «сломал код», но не от «умер диск»:
# удалённого репозитория у проекта нет, и вся история лежит в одном месте.
# git bundle сворачивает репозиторий целиком — все ветки и все коммиты —
# в один файл, из которого потом клонируется рабочая копия:
#
#   git clone часы-2026-09-13.bundle часы
#
# Плата для этого годится: она стоит отдельно и включена постоянно. Это не
# замена настоящему удалённому репозиторию, а дешёвая страховка до него.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="часы-$(date +%Y-%m-%d-%H%M).bundle"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

printf '\n\033[1;36m==> Сворачиваю историю\033[0m\n'
git -C "$HERE" bundle create "$TMP/$NAME" --all
SIZE=$(du -k "$TMP/$NAME" | cut -f1)
COMMITS=$(git -C "$HERE" rev-list --all --count)
printf '    %s КБ, коммитов: %s\n' "$SIZE" "$COMMITS"

# Целостность: битый бандл хуже отсутствующего — на него надеются.
git -C "$HERE" bundle verify "$TMP/$NAME" >/dev/null 2>&1 \
    && printf '    \033[1;32m[ok]\033[0m бандл проверен\n' \
    || { printf '    \033[1;31m[!!]\033[0m бандл битый, копировать нечего\n'; exit 1; }

if [ "${1:-}" = "--to" ]; then
    DEST="${2:?укажи папку: bash backup-repo.sh --to D:/backup}"
    mkdir -p "$DEST"
    cp "$TMP/$NAME" "$DEST/"
    printf '\033[1;32m    готово: %s/%s\033[0m\n\n' "$DEST" "$NAME"
    exit 0
fi

TARGET="${1:-alex@192.168.1.205}"
REMOTE_DIR="backup-часы"

printf '\n\033[1;36m==> Копирую на %s\033[0m\n' "$TARGET"
ssh -o ConnectTimeout=8 "$TARGET" "mkdir -p ~/$REMOTE_DIR"
scp -q "$TMP/$NAME" "$TARGET:~/$REMOTE_DIR/"

# Держим последние пять: больше — только место на карте, которую и так бережём.
ssh "$TARGET" "cd ~/$REMOTE_DIR && ls -1t *.bundle 2>/dev/null | tail -n +6 | xargs -r rm --"
KEPT=$(ssh "$TARGET" "ls -1 ~/$REMOTE_DIR/*.bundle 2>/dev/null | wc -l")

printf '\033[1;32m    готово: копий на плате %s\033[0m\n' "$KEPT"
cat <<EOF

  Восстановление, если диск компьютера погибнет:

      scp $TARGET:~/$REMOTE_DIR/$NAME .
      git clone $NAME часы

EOF
