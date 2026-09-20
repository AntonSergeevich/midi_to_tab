#!/usr/bin/env bash
# Получить сертификат и включить HTTPS, когда заработал DNS.
# Запускать от root:  DOMAIN=naslux.ru bash certbot.sh
set -euo pipefail

DOMAIN="${DOMAIN:-naslux.ru}"
EMAIL="${EMAIL:-}"
APP_DIR=/opt/nasluh/app

echo "=== Проверка DNS ==="
MY_IP="$(curl -s --max-time 10 https://api.ipify.org || true)"
DOMAIN_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
echo "  IP сервера:      ${MY_IP:-неизвестен}"
echo "  A-запись домена: ${DOMAIN_IP:-НЕ НАЙДЕНА}"

if [ -z "$DOMAIN_IP" ]; then
    echo
    echo "Домен не разрешается в адрес. Сертификат не выдадут."
    echo "Проверьте в панели регистратора:"
    echo "  1. делегирован ли домен (прописаны ли NS-серверы);"
    echo "  2. созданы ли A-записи @ и www на ${MY_IP:-адрес сервера}."
    echo "Обновление DNS занимает от 15 минут до нескольких часов."
    exit 1
fi

if [ "$MY_IP" != "$DOMAIN_IP" ]; then
    echo
    echo "Домен указывает на $DOMAIN_IP, а сервер имеет адрес $MY_IP."
    read -r -p "Продолжить всё равно? [y/N] " answer
    [ "$answer" = "y" ] || exit 1
fi

echo
echo "=== Сертификат ==="
mkdir -p /var/www/certbot
if [ -n "$EMAIL" ]; then
    MAIL_ARGS=(--email "$EMAIL")
else
    MAIL_ARGS=(--register-unsafely-without-email)
fi

certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" -d "www.$DOMAIN" \
    --agree-tos "${MAIL_ARGS[@]}" --non-interactive

echo
echo "=== Включаю HTTPS ==="
sed "s/naslux\.ru/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/nasluh
nginx -t
systemctl reload nginx

echo
echo "Готово. Сайт: https://$DOMAIN"
echo "Обновление сертификата произойдёт само -- таймер certbot уже включён."
