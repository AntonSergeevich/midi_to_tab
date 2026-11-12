#!/usr/bin/env bash
# Вернуть данные NASLUX (аккаунты, балансы, треки, плейлисты) из архива
# старого сервера -- например, файлового бэкапа Beget после сбоя 02.11.2026.
#
# Запускать от root на уже установленном сервере (после install.sh):
#   bash /opt/nasluh/app/deploy/restore_data.sh /root/backup.tar.gz
#
# Архив может быть .tar, .tar.gz, .tgz или .zip -- скрипт сам найдёт в нём
# папку данных (ту, где лежит app.db: обычно opt/nasluh/data). Текущая папка
# данных не удаляется, а откладывается рядом (data.before-restore-<время>),
# чтобы её можно было вернуть. Ключи (nasluh.env) из архива НЕ берутся:
# после взлома они считаются утёкшими, на сервере остаются новые.
set -euo pipefail

ARCHIVE="${1:-}"
DATA=/opt/nasluh/data
[ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || { echo "Укажите путь к архиву: bash restore_data.sh /root/backup.tar.gz"; exit 1; }

WORK="$(mktemp -d /root/restore.XXXXXX)"
echo "=== Распаковка в $WORK (только папка данных, если найдётся) ==="
case "$ARCHIVE" in
    *.zip) command -v unzip >/dev/null || apt-get install -y -qq unzip
           unzip -q "$ARCHIVE" -d "$WORK" ;;
    *)     tar -xf "$ARCHIVE" -C "$WORK" ;;
esac

DB="$(find "$WORK" -type f -name app.db -path '*nasluh/data/*' | head -1)"
[ -n "$DB" ] || DB="$(find "$WORK" -type f -name app.db | head -1)"
[ -n "$DB" ] || { echo "В архиве не нашлось app.db -- это точно архив сервера NASLUX?"; exit 1; }
SRC="$(dirname "$DB")"
echo "  Нашлась папка данных: $SRC ($(du -sh "$SRC" | cut -f1))"

FREE_KB="$(df --output=avail -k /opt/nasluh | tail -1)"
NEED_KB="$(du -sk "$SRC" | cut -f1)"
[ "$FREE_KB" -gt "$NEED_KB" ] || { echo "Мало места на диске: нужно $((NEED_KB/1024)) МБ, свободно $((FREE_KB/1024)) МБ"; exit 1; }

echo "=== Остановка сайта ==="
systemctl stop nasluh

STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -d "$DATA" ]; then
    mv "$DATA" "$DATA.before-restore-$STAMP"
    echo "  Текущие данные отложены в $DATA.before-restore-$STAMP"
fi
cp -a "$SRC" "$DATA"
# модели (Whisper и др.) лучше скачать заново, чем тащить старые копии
chown -R nasluh:nasluh "$DATA"
chmod 700 "$DATA"

echo "=== Запуск сайта ==="
systemctl start nasluh
sleep 3
systemctl --no-pager --lines=0 status nasluh | head -3
rm -rf "$WORK"
echo
echo "Готово. Проверьте вход в свой аккаунт на naslux.ru."
echo "Если что-то не так -- вернуть как было:"
echo "  systemctl stop nasluh && rm -rf $DATA && mv $DATA.before-restore-$STAMP $DATA && systemctl start nasluh"
