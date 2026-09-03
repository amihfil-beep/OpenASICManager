#!/usr/bin/env bash

set -euo pipefail


ENV_FILE="/etc/openasicmanager/openasicmanager.env"

NGINX_SITE="/etc/nginx/sites-available/openasicmanager"
NGINX_LINK="/etc/nginx/sites-enabled/openasicmanager"

HTPASSWD="/etc/nginx/.htpasswd-openasicmanager"

ACME_ROOT="/var/www/letsencrypt"


usage() {

    cat <<'USAGE'
Usage:

  sudo ./configure-web.sh \
      --domain manager.example.com \
      --email admin@example.com \
      --user admin

Requirements:

  - DNS A/AAAA record must point to this server
  - TCP/80 must be reachable for Let's Encrypt
  - OpenASICManager must already be installed
USAGE
}


DOMAIN=""
EMAIL=""
AUTH_USER=""


while [ "$#" -gt 0 ]; do

    case "$1" in

        --domain)
            DOMAIN="${2:-}"
            shift 2
            ;;

        --email)
            EMAIL="${2:-}"
            shift 2
            ;;

        --user)
            AUTH_USER="${2:-}"
            shift 2
            ;;

        -h|--help)
            usage
            exit 0
            ;;

        *)
            echo "ERROR: unknown argument: $1"
            usage
            exit 1
            ;;
    esac

done


if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root"
    exit 1
fi


if [ -z "$DOMAIN" ]; then
    echo "ERROR: --domain is required"
    exit 1
fi


if [ -z "$EMAIL" ]; then
    echo "ERROR: --email is required"
    exit 1
fi


if [ -z "$AUTH_USER" ]; then
    echo "ERROR: --user is required"
    exit 1
fi


if [ ! -f "$ENV_FILE" ]; then

    echo "ERROR:"
    echo "OpenASICManager configuration not found:"
    echo "  $ENV_FILE"
    echo
    echo "Run install.sh first."

    exit 1
fi


set_env() {

    local key="$1"
    local value="$2"


    if grep -q \
        "^${key}=" \
        "$ENV_FILE"
    then

        sed -i \
            "s|^${key}=.*|${key}=${value}|" \
            "$ENV_FILE"

    else

        printf \
            '%s=%s\n' \
            "$key" \
            "$value" \
            >> "$ENV_FILE"

    fi
}


echo "========================================"
echo " OpenASICManager Web Configuration"
echo "========================================"


echo
echo "[1/7] Installing nginx / certbot..."

export DEBIAN_FRONTEND=noninteractive

apt-get update

apt-get install -y \
    nginx \
    apache2-utils \
    certbot


echo
echo "[2/7] Preparing ACME webroot..."

install \
    -d \
    -o www-data \
    -g www-data \
    -m 0755 \
    "$ACME_ROOT"


echo
echo "[3/7] Configuring Basic Auth..."

echo
echo "Create/update password for:"
echo "  $AUTH_USER"
echo

htpasswd \
    "$HTPASSWD" \
    "$AUTH_USER"

chmod 0640 \
    "$HTPASSWD"

chown \
    root:www-data \
    "$HTPASSWD"


echo
echo "[4/7] Installing temporary HTTP nginx config..."

cat > "$NGINX_SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;

    server_name ${DOMAIN};

    server_tokens off;

    location ^~ /.well-known/acme-challenge/ {
        root ${ACME_ROOT};
        default_type text/plain;
        auth_basic off;
    }

    location / {
        return 200 "OpenASICManager HTTPS setup\n";
        add_header Content-Type text/plain;
    }
}
NGINX


ln -sfn \
    "$NGINX_SITE" \
    "$NGINX_LINK"


nginx -t

systemctl reload \
    nginx


echo
echo "[5/7] Requesting Let's Encrypt certificate..."

certbot certonly \
    --webroot \
    -w "$ACME_ROOT" \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive


CERT="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
KEY="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"


if [ ! -f "$CERT" ] \
   || [ ! -f "$KEY" ]; then

    echo "ERROR: certificate was not created"
    exit 1
fi


echo
echo "[6/7] Installing HTTPS nginx config..."

cat > "$NGINX_SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;

    server_name ${DOMAIN};

    location ^~ /.well-known/acme-challenge/ {
        root ${ACME_ROOT};
        default_type text/plain;
        auth_basic off;
    }

    location / {
        return 301 https://${DOMAIN}\$request_uri;
    }
}


server {
    listen 443 ssl;
    listen [::]:443 ssl;

    server_name ${DOMAIN};

    server_tokens off;

    ssl_certificate ${CERT};
    ssl_certificate_key ${KEY};

    ssl_protocols TLSv1.2 TLSv1.3;

    ssl_session_timeout 1d;
    ssl_session_cache shared:OpenASICSSL:10m;
    ssl_session_tickets off;

    auth_basic "OpenASICManager";
    auth_basic_user_file ${HTPASSWD};

    client_max_body_size 10m;

    add_header \
        X-Content-Type-Options \
        "nosniff" \
        always;

    add_header \
        X-Frame-Options \
        "SAMEORIGIN" \
        always;

    add_header \
        Referrer-Policy \
        "same-origin" \
        always;


    location / {

        proxy_pass \
            http://127.0.0.1:8088;

        proxy_http_version 1.1;

        proxy_set_header \
            Host \
            \$host;

        proxy_set_header \
            X-Real-IP \
            \$remote_addr;

        proxy_set_header \
            X-Forwarded-For \
            \$proxy_add_x_forwarded_for;

        proxy_set_header \
            X-Forwarded-Proto \
            https;

        proxy_set_header \
            X-Remote-User \
            \$remote_user;

        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        proxy_buffering off;
    }
}
NGINX


nginx -t

systemctl reload \
    nginx


echo
echo "[7/7] Updating OpenASICManager configuration..."

set_env \
    PUBLIC_DOMAIN \
    "$DOMAIN"


# Remote Web hosts use the primary web domain
# by default:
#
# m192-168-1-81.manager.example.com
#
set_env \
    REMOTE_WEB_BASE_DOMAIN \
    "$DOMAIN"

set_env \
    REMOTE_WEB_COOKIE_DOMAIN \
    ".${DOMAIN}"


systemctl restart \
    openasicmanager.service


# Reload nginx after successful certificate renewal.
install \
    -d \
    -m 0755 \
    /etc/letsencrypt/renewal-hooks/deploy

cat > \
    /etc/letsencrypt/renewal-hooks/deploy/openasicmanager-nginx \
    <<'HOOK'
#!/usr/bin/env bash

set -e

nginx -t
systemctl reload nginx
HOOK

chmod 0755 \
    /etc/letsencrypt/renewal-hooks/deploy/openasicmanager-nginx


if systemctl list-unit-files \
    certbot.timer \
    >/dev/null 2>&1
then

    systemctl enable \
        --now \
        certbot.timer \
        >/dev/null 2>&1 \
        || true
fi


echo
echo "========================================"
echo " Web configuration completed"
echo "========================================"

echo
echo "URL:"
echo "  https://${DOMAIN}"

echo
echo "Basic Auth user:"
echo "  ${AUTH_USER}"

echo
echo "Certificate:"
echo "  ${CERT}"

echo
echo "Remote ASIC hostname format:"
echo "  m192-168-1-81.${DOMAIN}"
