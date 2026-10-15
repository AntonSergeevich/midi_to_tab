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
cp "$APP_DIR/deploy/nasluh-watchdog.service" /etc/systemd/system/
cp "$APP_DIR/deploy/nasluh-watchdog.timer" /etc/systemd/system/
# Юнит вебхука не перезапускаем: этот скрипт сам запущен им же, и
# systemctl restart убьёт всю его cgroup -- включая нас самих на полпути.
cp "$APP_DIR/deploy/nasluh-deploy-webhook.service" /etc/systemd/system/
mkdir -p /opt/nasluh/data/cache
chown -R nasluh:nasluh /opt/nasluh/data
systemctl daemon-reload
# Сторож: зависший (не упавший) сайт перезапускается сам, со снимком потоков
systemctl enable --now nasluh-watchdog.timer

echo
echo "=== Шумодав для записанного голоса (DeepFilterNet) ==="
# Одна статическая программа с моделью внутри (MIT/Apache-2.0). Ставится
# один раз; не скачалась (нет сети, сменилась сумма) -- сайт работает,
# голос чистится запасным шумодавом ffmpeg. Деплой из-за этого не падает.
DF_VER=0.5.6
DF_SHA=70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da
DF_BIN=/opt/nasluh/bin/deep-filter
if ! echo "$DF_SHA  $DF_BIN" | sha256sum -c --status 2>/dev/null; then
    mkdir -p /opt/nasluh/bin
    tmp=$(mktemp)
    if curl -fsSL --max-time 300 -o "$tmp" \
        "https://github.com/Rikorose/DeepFilterNet/releases/download/v$DF_VER/deep-filter-$DF_VER-x86_64-unknown-linux-musl" \
        && echo "$DF_SHA  $tmp" | sha256sum -c --status; then
        install -m 755 "$tmp" "$DF_BIN"
        echo "deep-filter $DF_VER поставлен"
    else
        echo "ВНИМАНИЕ: deep-filter не скачался или сумма не совпала -- шумодав будет запасной (ffmpeg)"
    fi
    rm -f "$tmp"
else
    echo "deep-filter уже стоит"
fi

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
