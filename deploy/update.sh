#!/usr/bin/env bash
# Обновить код и перезапустить службу.
# Запускать от root:  bash /opt/nasluh/app/deploy/update.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nasluh/app}"
BRANCH="${BRANCH:-claude/epic-mccarthy-sl30hz}"

# Папка принадлежит пользователю nasluh, а команда идёт от root:
# без этой строки git откажется с "detected dubious ownership".
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

echo "=== Обновление кода ==="
git -C "$APP_DIR" fetch origin "$BRANCH"
git -C "$APP_DIR" reset --hard "origin/$BRANCH"
chown -R nasluh:nasluh "$APP_DIR"

echo
echo "=== Настройки службы ==="
# Юниты могли измениться -- перекладываем и перечитываем
cp "$APP_DIR/deploy/nasluh.service" /etc/systemd/system/
cp "$APP_DIR/deploy/nasluh-cleanup.service" /etc/systemd/system/
cp "$APP_DIR/deploy/nasluh-cleanup.timer" /etc/systemd/system/
# Юнит вебхука не перезапускаем: этот скрипт сам запущен им же, и
# systemctl restart убьёт всю его cgroup -- включая нас самих на полпути.
cp "$APP_DIR/deploy/nasluh-deploy-webhook.service" /etc/systemd/system/
mkdir -p /opt/nasluh/data/cache
chown -R nasluh:nasluh /opt/nasluh/data
systemctl daemon-reload

echo
echo "=== Nginx ==="
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/nasluh
if nginx -t; then
    systemctl reload nginx
    echo "nginx перечитал конфиг"
else
    echo "ВНИМАНИЕ: nginx -t не прошёл, новый конфиг НЕ применён (старый остаётся активным)"
fi

echo
echo "=== Перезапуск ==="
systemctl restart nasluh
sleep 2
systemctl --no-pager status nasluh | head -6

echo
echo "Готово. Версия:"
git -C "$APP_DIR" log --oneline -1
