#!/usr/bin/env bash
# Install or update Together Habits Bot on a fresh Ubuntu/Debian VPS.
# Run as root:  curl -fsSL <raw URL of this file> | bash
# Re-running the script updates the bot to the latest code and restarts it.
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/YTSuno796765/together-habits-bot.git}"
APP_DIR=/opt/together-habits-bot
DATA_DIR=/var/lib/together-habits
ENV_FILE=/etc/together-habits.env
SERVICE=together-habits

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this script as root (or with sudo)." >&2
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl python3 python3-venv >/dev/null

echo "==> Creating service user"
id -u habits >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin habits
install -d -o habits -g habits -m 700 "$DATA_DIR"

echo "==> Fetching code"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only -q
else
  git clone -q "$REPO_URL" "$APP_DIR"
fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"
# Same version number on every update, so force-refresh the package itself.
"$APP_DIR/.venv/bin/pip" install -q --force-reinstall --no-deps "$APP_DIR"

if [[ ! -s "$ENV_FILE" ]]; then
  echo
  echo "==> Paste the bot token from @BotFather and press Enter (input is hidden):"
  read -rs TOKEN < /dev/tty
  echo
  if [[ -z "$TOKEN" ]]; then
    echo "Token is empty, aborting." >&2
    exit 1
  fi
  umask 077
  printf 'TELEGRAM_BOT_TOKEN=%s\nHABITS_DB_PATH=%s/habits.db\nHABITS_TIMEZONE=%s\n' \
    "$TOKEN" "$DATA_DIR" "${HABITS_TIMEZONE:-Europe/Moscow}" > "$ENV_FILE"
  unset TOKEN
fi
chmod 600 "$ENV_FILE"

echo "==> Setting up HTTPS for the Mini App"
# Telegram opens Mini Apps only over HTTPS. Without a domain we use the free
# <ip-with-dashes>.sslip.io name, and Caddy obtains a certificate for it.
if ! grep -q '^WEBAPP_URL=' "$ENV_FILE"; then
  PUBLIC_IP="${PUBLIC_IP:-$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)}"
  if [[ ! "$PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Could not detect the public IPv4 address. Re-run with PUBLIC_IP=<address>." >&2
    exit 1
  fi
  printf 'WEBAPP_URL=https://%s.sslip.io\nWEBAPP_HOST=127.0.0.1\nWEBAPP_PORT=8080\n' \
    "${PUBLIC_IP//./-}" >> "$ENV_FILE"
fi
DOMAIN="$(sed -n 's|^WEBAPP_URL=https://||p' "$ENV_FILE" | head -n 1)"
WEB_PORT="$(sed -n 's|^WEBAPP_PORT=||p' "$ENV_FILE" | head -n 1)"
WEB_PORT="${WEB_PORT:-8080}"

if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
fi
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$WEB_PORT
}
CADDY
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
fi
systemctl enable -q caddy
systemctl restart caddy

echo "==> Configuring systemd service"
cat > "/etc/systemd/system/$SERVICE.service" <<UNIT
[Unit]
Description=Together Habits Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=habits
WorkingDirectory=$DATA_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python -m together_habits
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"
sleep 3

if systemctl is-active -q "$SERVICE"; then
  echo "==> Waiting for the HTTPS certificate (up to 90 s)"
  web_ok=""
  for _ in $(seq 1 18); do
    if curl -fsS --max-time 5 "https://$DOMAIN/healthz" >/dev/null 2>&1; then
      web_ok=1
      break
    fi
    sleep 5
  done
  echo
  echo "Bot is running. Open it in Telegram and send /start."
  echo "Code version: $(git -C "$APP_DIR" log -1 --format='%h  %ad  %s' --date=format:'%Y-%m-%d %H:%M')"
  if [[ -n "$web_ok" ]]; then
    echo "Mini App: https://$DOMAIN (button «Привычки» next to the message field)"
  else
    echo "Mini App is not reachable over HTTPS yet. Check: journalctl -u caddy -n 30"
  fi
  echo "Logs:    journalctl -u $SERVICE -f"
  echo "Update:  run this script again"
  echo "Token:   edit $ENV_FILE, then: systemctl restart $SERVICE"
else
  echo "Bot failed to start. Recent logs:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  exit 1
fi
