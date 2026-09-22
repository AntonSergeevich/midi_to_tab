#!/usr/bin/env bash
# Установка автодеплоя по вебхуку GitHub: пуш в ветку -> сервер сам
# подтягивает код и перезапускается, без ручного update.sh по SSH.
#
# Запускать от root на уже установленном сервере (после install.sh):
#   bash install_deploy_webhook.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nasluh/app}"
ENV_FILE=/opt/nasluh/deploy-webhook.env
NGINX_CONF=/etc/nginx/sites-available/nasluh

say() { echo; echo "=== $* ==="; }

say "Секрет"
if [ ! -f "$ENV_FILE" ]; then
    cp "$APP_DIR/deploy/deploy-webhook.env.example" "$ENV_FILE"
    # Заглушка в файле -- дыра, а не напоминание: генерируем сразу.
    SECRET="$(openssl rand -hex 32)"
    sed -i "s|DEPLOY_WEBHOOK_SECRET=.*|DEPLOY_WEBHOOK_SECRET=$SECRET|" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Секрет сгенерирован: $SECRET"
    echo "Этот же секрет вставьте в GitHub при добавлении вебхука (см. ниже)."
else
    echo "Файл $ENV_FILE уже есть, секрет не трогаю."
    echo "Текущий (для вебхука в GitHub):"
    grep DEPLOY_WEBHOOK_SECRET "$ENV_FILE"
fi

say "Служба"
cp "$APP_DIR/deploy/nasluh-deploy-webhook.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nasluh-deploy-webhook.service
sleep 1
systemctl --no-pager status nasluh-deploy-webhook.service | head -6

say "nginx"
if [ ! -f "$NGINX_CONF" ]; then
    echo "  $NGINX_CONF не найден -- сначала запустите install.sh."
    exit 1
fi
if grep -q "internal/deploy-webhook" "$NGINX_CONF"; then
    echo "  Путь /internal/deploy-webhook в nginx уже настроен, пропускаю."
else
    # Вставляем блок location перед основным "location / {" -- у nginx
    # выбор идёт по самому длинному совпадающему префиксу, но так
    # нагляднее читать сам файл.
    python3 - "$NGINX_CONF" <<'PY'
import sys

path = sys.argv[1]
block = """    location /internal/deploy-webhook {
        proxy_pass http://127.0.0.1:8099;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

"""
text = open(path, encoding="utf-8").read()
marker = "    location / {\n"
i = text.index(marker)
text = text[:i] + block + text[i:]
open(path, "w", encoding="utf-8").write(text)
PY
    nginx -t
    systemctl reload nginx
    echo "  Добавлено."
fi

say "Готово"
echo "Добавьте вебхук в GitHub:"
echo "  Репозиторий -> Settings -> Webhooks -> Add webhook"
echo "    Payload URL:  https://naslux.ru/internal/deploy-webhook"
echo "    Content type: application/json"
echo "    Secret:       (значение выше)"
echo "    Events:       Just the push event"
echo
echo "Логи:  journalctl -u nasluh-deploy-webhook -f"
