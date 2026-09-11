#!/usr/bin/env bash
# Desk Companion — шаг 2: библиотеки для самого приложения.
#
#   sudo bash setup-step2.sh            # всё, кроме машинного обучения
#   sudo bash setup-step2.sh --with-ml  # плюс scikit-learn для Режима 6
#
# Ставим через apt, а не pip. Две причины. На Raspberry Pi OS Bookworm и
# новее pip вне виртуального окружения заблокирован (PEP 668) — попытка
# установки просто отваливается с ошибкой. И для ARM в репозитории лежат
# уже собранные пакеты, тогда как pip стал бы компилировать numpy и Pillow
# из исходников: на Zero 2 W это часы работы и риск упереться в память.
#
# scikit-learn отдельным флагом: он тянет scipy и занимает под двести
# мегабайт, а нужен только Режиму 6, который всё равно нельзя включать,
# пока не накопятся 1-2 недели наблюдений (п.10 плана).

set -euo pipefail

WITH_ML=0
[ "${1:-}" = "--with-ml" ] && WITH_ML=1

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m[ok]\033[0m %s\n' "$*"; }
bad()  { printf '    \033[1;31m[!!]\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Нужны права root. Запускай: sudo bash $0" >&2
    exit 1
fi

TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"

# ---------------------------------------------------------------------------
log "Библиотеки Python"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

PACKAGES=(
    python3-pil        # Pillow — вся отрисовка экрана
    python3-numpy      # перегон кадра в RGB565, сравнение полос
    python3-spidev     # шина SPI: экран и тач
    python3-gpiozero   # GPIO: энкодер, DC/RESET, подсветка
    python3-lgpio      # бэкенд gpiozero, на Bookworm он основной
    python3-serial     # UART для радара
    python3-smbus2     # I2C для датчика воздуха
    python3-paho-mqtt  # обмен с ПК-агентом
)
[ "$WITH_ML" -eq 1 ] && PACKAGES+=(python3-sklearn)

apt-get install -y --no-install-recommends "${PACKAGES[@]}"
ok "установлено пакетов: ${#PACKAGES[@]}"

# ---------------------------------------------------------------------------
log "Проверка импортов"
# Проверяем от имени пользователя, под которым пойдёт сервис: у root могут
# быть другие пути, и «у меня работает» превратилось бы в ложную проверку.
FAILED=0
while read -r module label; do
    if sudo -u "$TARGET_USER" python3 -c "import $module" 2>/dev/null; then
        ok "$label"
    else
        bad "$label — не импортируется"
        FAILED=1
    fi
done <<'MODULES'
PIL отрисовка (Pillow)
numpy массивы (numpy)
spidev шина_SPI (spidev)
gpiozero GPIO (gpiozero)
serial UART (pyserial)
smbus2 шина_I2C (smbus2)
paho.mqtt.client MQTT (paho-mqtt)
MODULES

if [ "$WITH_ML" -eq 1 ]; then
    if sudo -u "$TARGET_USER" python3 -c "import sklearn" 2>/dev/null; then
        ok "машинное обучение (scikit-learn)"
    else
        bad "scikit-learn не импортируется"
        FAILED=1
    fi
else
    echo "    --  scikit-learn не ставился: Режим 6 будет молчать до переобучения"
fi

# ---------------------------------------------------------------------------
log "Доступ к устройствам"
for group in spi gpio i2c dialout; do
    if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx "$group"; then
        ok "$TARGET_USER состоит в группе $group"
    else
        bad "$TARGET_USER не в группе $group — устройство не откроется"
        FAILED=1
    fi
done

# ---------------------------------------------------------------------------
log "Готовность приложения"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if sudo -u "$TARGET_USER" python3 -c "
import sys; sys.path.insert(0, '$APP_DIR')
from app.screens.registry import load, load_config
cfg = load_config('$APP_DIR/app/config.toml')
print(f'    экранов в конфиге: {len(cfg[\"screens\"][\"enabled\"])}')
" 2>/dev/null; then
    ok "конфиг читается, экраны собираются"
else
    bad "приложение не импортируется — проверь, что каталог скопирован целиком"
    FAILED=1
fi

echo
if [ "$FAILED" -eq 0 ]; then
    cat <<EOF
    Всё на месте. Дальше — проверка железа по узлам, по мере пайки:

        python3 tools/bringup.py kernel
        python3 tools/bringup.py display

    Полный список шагов: kernel i2c display touch encoder radar air

EOF
else
    echo "    Есть проблемы — смотри строки [!!] выше."
    echo
    exit 1
fi
