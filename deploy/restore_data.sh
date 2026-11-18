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
[ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || { echo "Укажите архив: bash restore_data.sh /root/backup_naslux.tar [--apply]"; exit 1; }

echo "=== Ищем в архиве базу (app.db) ==="
case "$ARCHIVE" in
    *.zip) command -v unzip >/dev/null || apt-get install -y -qq unzip
           LIST="$(unzip -Z1 "$ARCHIVE")" ;;
    *)     LIST="$(tar -tf "$ARCHIVE")" ;;
esac
DBPATH="$(printf '%s\n' "$LIST" | grep -E '(^|/)nasluh/data/app\.db$' | head -1 || true)"
[ -n "$DBPATH" ] || DBPATH="$(printf '%s\n' "$LIST" | grep -E '(^|/)app\.db$' | head -1 || true)"
[ -n "$DBPATH" ] || { echo "В архиве нет app.db -- это точно архив сервера NASLUX? Первые строки архива:"; printf '%s\n' "$LIST" | head -20; exit 1; }
PREFIX="$(dirname "$DBPATH")"
echo "  Папка данных в архиве: $PREFIX"

WORK="$(mktemp -d "${NASLUX_WORK:-/root}/restore.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
echo "=== Распаковка только папки данных (без моделей) в $WORK ==="
case "$ARCHIVE" in
    *.zip) unzip -q "$ARCHIVE" "$PREFIX/*" -x "$PREFIX/models/*" -d "$WORK" ;;
    *)     tar -xf "$ARCHIVE" -C "$WORK" --exclude="$PREFIX/models" "$PREFIX" ;;
esac
SRC="$WORK/$PREFIX"
[ -f "$SRC/app.db" ] || { echo "Не распаковалось: $SRC/app.db"; exit 1; }
echo "  Из бэкапа: $(du -sh "$SRC" | cut -f1)"

FREE_KB="$(df --output=avail -k "$(dirname "$DATA")" | tail -1)"
NEED_KB="$(( $(du -sk "$SRC" | cut -f1) + $(du -sk "$DATA" 2>/dev/null | cut -f1 || echo 0) ))"
[ "$FREE_KB" -gt "$NEED_KB" ] || { echo "Мало места: нужно $((NEED_KB/1024)) МБ, свободно $((FREE_KB/1024)) МБ"; exit 1; }

if [ "$MODE" != "--apply" ] && [ "$MODE" != "--replace" ]; then
    echo "=== ПЛАН объединения (ничего не меняю) ==="
    cp -a "$SRC" "$WORK/plan-into"
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
cp -a "$SRC" "$DATA"
# модели (Whisper и др.) -- с нового сервера, не скачивать заново
[ -d "$BEFORE/models" ] && [ ! -e "$DATA/models" ] && cp -a "$BEFORE/models" "$DATA/models"
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
