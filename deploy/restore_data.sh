#!/usr/bin/env bash
# Вернуть данные NASLUX (аккаунты, балансы, треки, плейлисты) из архива
# старого сервера -- файлового бэкапа Beget после сбоя 02.11.2026.
#
# Запускать от root на уже установленном сервере:
#   bash /opt/nasluh/app/deploy/restore_data.sh /root/backup_naslux.tar          # план, ничего не меняет
#   bash /opt/nasluh/app/deploy/restore_data.sh /root/backup_naslux.tar --apply  # объединить
#   (--replace вместо объединения -- взять только бэкап, без нового; не советую)
#
# По умолчанию ОБЪЕДИНЯЕТ (deploy/merge_data.py): основа -- база из бэкапа,
# плюс всё, что появилось на новом сервере после переезда (новые люди,
# треки, оплаты, новые пароли). Текущая папка данных не удаляется, а
# откладывается рядом (data.before-restore-<время>) -- вернуть одной командой.
# Из архива берётся только папка данных (где лежит app.db), без моделей.
# Ключи из архива НЕ берутся: после взлома они считаются утёкшими.
set -euo pipefail

ARCHIVE="${1:-}"
MODE="${2:-plan}"
DATA="${NASLUX_DATA:-/opt/nasluh/data}"
APP="${NASLUX_APP:-/opt/nasluh/app}"
PY="${NASLUX_PY:-/opt/nasluh/venv/bin/python}"
SVC="${NASLUX_SYSTEMCTL:-systemctl}"      # подменяются только в тестах
OWN="${NASLUX_CHOWN:-chown}"
[ -n "$ARCHIVE" ] || { echo "Укажите архив: bash restore_data.sh /root/backup_naslux.tar [--apply]"; exit 1; }
if [ ! -f "$ARCHIVE" ]; then
    echo "Файла $ARCHIVE на сервере нет. Архивы, которые здесь есть:"
    find /root /home /tmp -maxdepth 2 -type f \( -name '*.tar' -o -name '*.tar.gz' -o -name '*.tgz' -o -name '*.zip' \) \
        -printf '  %p  (%s байт)\n' 2>/dev/null | head -10
    echo "Если пусто -- отправьте архив с компьютера (PowerShell на компьютере, не здесь):"
    echo "  scp D:\\Downloads\\backup_naslux.tar root@<IP сервера>:/root/"
    exit 1
fi

echo "=== Ищем в архиве папку данных (большой архив -- пара минут) ==="
PROBE="$("$PY" -I "$APP/deploy/backup_probe.py" "$ARCHIVE")" || exit 1
PREFIX="${PROBE% *}"
SIZE_KB="${PROBE##* }"
echo "  Папка данных в архиве: $PREFIX, $((SIZE_KB / 1024)) МБ без моделей"

# Место: распакованная папка данных + запас 1 ГБ (треки нового сервера
# подключаются жёсткими ссылками, модели -- тоже, лишнего места не берут)
FREE_KB="$(df --output=avail -k "$(dirname "$DATA")" | tail -1)"
NEED_KB="$(( SIZE_KB + 1048576 ))"
if [ "$FREE_KB" -lt "$NEED_KB" ]; then
    echo "Мало места: нужно ~$((NEED_KB / 1024)) МБ, свободно $((FREE_KB / 1024)) МБ."
    echo "Сам архив занимает $(du -h "$ARCHIVE" | cut -f1) -- можно распаковывать прямо с компьютера, напишите Claude."
    exit 1
fi

WORK="$(mktemp -d "${NASLUX_WORK:-/root}/restore.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
echo "=== Распаковка только папки данных (без моделей) ==="
case "$ARCHIVE" in
    *.zip) unzip -q "$ARCHIVE" "$PREFIX/*" -x "$PREFIX/models/*" -d "$WORK" ;;
    *)     tar -xf "$ARCHIVE" -C "$WORK" --exclude="$PREFIX/models" "$PREFIX" ;;
esac
SRC="$WORK/$PREFIX"
[ -f "$SRC/app.db" ] || { echo "Не распаковалось: $SRC/app.db"; exit 1; }
echo "  Из бэкапа: $(du -sh "$SRC" | cut -f1)"

if [ "$MODE" != "--apply" ] && [ "$MODE" != "--replace" ]; then
    echo "=== ПЛАН объединения (ничего не меняю) ==="
    # для плана нужна только сама база -- не копируем треки
    mkdir "$WORK/plan-into" && cp -a "$SRC"/app.db* "$WORK/plan-into/"
    MIDI2TAB_DATA="$WORK/plan-into" "$PY" "$APP/deploy/merge_data.py" --into "$WORK/plan-into" --from "$DATA" --dry
    echo
    echo "Всё верно? Запустите то же с --apply в конце."
    exit 0
fi

echo "=== Остановка сайта ==="
"$SVC" stop nasluh
STAMP="$(date +%Y%m%d-%H%M%S)"
BEFORE="$DATA.before-restore-$STAMP"
mv "$DATA" "$BEFORE"
echo "  Текущие данные отложены в $BEFORE"
# распакованное -- на место переносом (тот же диск: мгновенно и без места)
mv "$SRC" "$DATA" 2>/dev/null || cp -a "$SRC" "$DATA"
# модели (Whisper и др.) -- с нового сервера жёсткими ссылками: не качать заново и не занимать места
if [ -d "$BEFORE/models" ] && [ ! -e "$DATA/models" ]; then
    cp -al "$BEFORE/models" "$DATA/models" 2>/dev/null || cp -a "$BEFORE/models" "$DATA/models"
fi
if [ "$MODE" = "--apply" ]; then
    echo "=== Объединение: бэкап + новое с этого сервера ==="
    if ! MIDI2TAB_DATA="$DATA" "$PY" "$APP/deploy/merge_data.py" --into "$DATA" --from "$BEFORE"; then
        echo "Объединение не удалось -- возвращаю как было"
        rm -rf "$DATA" && mv "$BEFORE" "$DATA" && "$OWN" -R nasluh:nasluh "$DATA" && "$SVC" start nasluh
        exit 1
    fi
fi
"$OWN" -R nasluh:nasluh "$DATA"
chmod 700 "$DATA"

echo "=== Запуск сайта ==="
"$SVC" start nasluh
sleep "${NASLUX_WAIT:-3}"
"$SVC" --no-pager --lines=0 status nasluh | head -3 || true
echo
echo "Готово. Проверьте вход в свой аккаунт на naslux.ru и старые треки в Студии."
echo "Если что-то не так -- вернуть как было:"
echo "  systemctl stop nasluh && rm -rf $DATA && mv $BEFORE $DATA && chown -R nasluh:nasluh $DATA && systemctl start nasluh"
