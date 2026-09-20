#!/usr/bin/env bash
# Установка НАСЛУХ на чистый сервер Ubuntu/Debian.
# Запускать от root:  bash install.sh
set -euo pipefail

DOMAIN="${DOMAIN:-naslux.ru}"
# Почта для Let's Encrypt: на неё придёт предупреждение, если сертификат
# перестанет обновляться. Без неё такое письмо просто некуда отправить.
EMAIL="${EMAIL:-}"
APP_DIR=/opt/nasluh/app
VENV=/opt/nasluh/venv
DATA=/opt/nasluh/data
REPO="${REPO:-https://github.com/AntonSergeevich/midi_to_tab}"
BRANCH="${BRANCH:-claude/epic-mccarthy-sl30hz}"

say() { echo; echo "=== $* ==="; }

say "Проверка домена"
# Certbot не выдаст сертификат, если домен не указывает на этот сервер.
# Проверяем заранее, чтобы не узнать об этом на середине установки.
MY_IP="$(curl -s --max-time 10 https://api.ipify.org || true)"
DOMAIN_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
echo "  IP сервера:  ${MY_IP:-неизвестен}"
echo "  A-запись $DOMAIN: ${DOMAIN_IP:-не найдена}"
if [ -n "$MY_IP" ] && [ -n "$DOMAIN_IP" ] && [ "$MY_IP" != "$DOMAIN_IP" ]; then
    echo "  ВНИМАНИЕ: домен указывает не на этот сервер. Сертификат не выдадут."
    echo "  Поправьте A-запись и подождите обновления DNS."
    read -r -p "  Продолжить всё равно? [y/N] " answer
    [ "$answer" = "y" ] || exit 1
fi

say "Пакеты"
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential git \
    nginx certbot python3-certbot-nginx ufw fail2ban ffmpeg curl

say "Пользователь и папки"
# Сервис работает не от root: если его взломают, чужой получит доступ
# только к его собственным файлам.
id -u nasluh >/dev/null 2>&1 || useradd --system --home /opt/nasluh --shell /usr/sbin/nologin nasluh
mkdir -p "$APP_DIR" "$DATA"/{uploads,results,models}

say "Код"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch origin "$BRANCH"
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
    git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

say "Окружение Python"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements-web.txt"
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements-audio.txt"
"$VENV/bin/pip" install --quiet --no-deps basic-pitch

# pygame намеренно не ставится: он нужен только настольному окну для
# прослушивания, а в вебе звук синтезируется в браузере. Под Python 3.14
# готовой сборки у него нет, и попытка установки ломала бы развёртывание.

echo "Разделение на партии и распознавание текста ставятся отдельно:"
echo "  $VENV/bin/pip install -r $APP_DIR/requirements-separation.txt"
echo "  $VENV/bin/pip install faster-whisper"
echo "Они тянут PyTorch (около 2 ГБ). Ставьте, когда убедитесь, что памяти хватает."

say "Настройки"
if [ ! -f /opt/nasluh/nasluh.env ]; then
    cp "$APP_DIR/deploy/nasluh.env.example" /opt/nasluh/nasluh.env
    # Ключи генерируются сразу: оставленная заглушка -- это дыра,
    # а не напоминание, и про неё забывают.
    sed -i "s|MIDI2TAB_SECRET=.*|MIDI2TAB_SECRET=$(openssl rand -hex 32)|" /opt/nasluh/nasluh.env
    sed -i "s|MIDI2TAB_ADMIN_KEY=.*|MIDI2TAB_ADMIN_KEY=$(openssl rand -hex 16)|" /opt/nasluh/nasluh.env
    chmod 600 /opt/nasluh/nasluh.env
    echo "Ключи сгенерированы. Ключ админки:"
    grep MIDI2TAB_ADMIN_KEY /opt/nasluh/nasluh.env
fi
chown -R nasluh:nasluh /opt/nasluh

say "Служба"
cp "$APP_DIR/deploy/nasluh.service" /etc/systemd/system/
cp "$APP_DIR/deploy/nasluh-cleanup.service" /etc/systemd/system/
cp "$APP_DIR/deploy/nasluh-cleanup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nasluh.service
systemctl enable --now nasluh-cleanup.timer

say "nginx"
sed "s/naslux\.ru/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/nasluh
ln -sf /etc/nginx/sites-available/nasluh /etc/nginx/sites-enabled/nasluh
rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/certbot

# До получения сертификата конфиг с HTTPS не запустится -- сначала
# поднимаем только HTTP, чтобы certbot смог пройти проверку.
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    # Временный конфиг на время, пока нет сертификата. Он полноценный:
    # по нему работают и до настройки DNS, обращаясь по адресу сервера,
    # поэтому здесь нужны и предел загрузки, и длинные таймауты.
    cat > /etc/nginx/sites-available/nasluh <<NGX
server {
    listen 80 default_server;   # отвечаем и по адресу сервера, не только по домену
    server_name $DOMAIN www.$DOMAIN _;

    client_max_body_size 80m;
    client_body_timeout 300s;
    proxy_connect_timeout 60s;
    proxy_send_timeout    600s;
    proxy_read_timeout    600s;

    access_log /var/log/nginx/nasluh.access.log;
    error_log  /var/log/nginx/nasluh.error.log;

    location /.well-known/acme-challenge/ { root /var/www/certbot; }

    location /static/ {
        alias $APP_DIR/web/static/;
        expires 1h;
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
    }
}
NGX
    nginx -t && systemctl reload nginx
    say "Сертификат"
    if [ -n "$EMAIL" ]; then
        CERT_MAIL=(--email "$EMAIL")
    else
        echo "Почта не указана (EMAIL=...). Письма о проблемах с сертификатом приходить не будут."
        CERT_MAIL=(--register-unsafely-without-email)
    fi
    # Неудача с сертификатом НЕ должна обрывать установку: сайт уже
    # работает по HTTP, и важнее доделать остальное -- брандмауэр,
    # автозапуск, уборку. Сертификат берётся отдельной командой, когда
    # заработает DNS.
    if certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" -d "www.$DOMAIN" \
        --agree-tos "${CERT_MAIL[@]}" --non-interactive; then
        sed "s/naslux\.ru/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/nasluh
        HTTPS_READY=1
    else
        echo
        echo "  Сертификат не выдан -- скорее всего, домен ещё не указывает сюда."
        echo "  Это не мешает работе: сайт доступен по адресу http://${MY_IP:-$DOMAIN}"
        echo "  Когда DNS заработает (dig +short $DOMAIN вернёт ${MY_IP:-адрес сервера}),"
        echo "  выполните:  DOMAIN=$DOMAIN bash $APP_DIR/deploy/certbot.sh"
        echo
        HTTPS_READY=0
    fi
fi

nginx -t && systemctl reload nginx

say "Брандмауэр"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
systemctl enable --now fail2ban

say "Готово"
systemctl --no-pager status nasluh.service | head -5
echo
if [ "${HTTPS_READY:-1}" = "1" ] && [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "Сайт:    https://$DOMAIN"
    echo "Админка: https://$DOMAIN/admin"
else
    echo "Сайт пока по адресу сервера (DNS или сертификат ещё не готовы):"
    echo "  http://${MY_IP:-проверьте IP}"
    echo "  http://${MY_IP:-проверьте IP}/admin"
    echo
    echo "После настройки DNS получите сертификат:"
    echo "  DOMAIN=$DOMAIN bash $APP_DIR/deploy/certbot.sh"
fi
echo "Ключ:    grep MIDI2TAB_ADMIN_KEY /opt/nasluh/nasluh.env"
echo "Логи:    journalctl -u nasluh -f"
