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
apt-get install -y -qq git python3 python3-venv >/dev/null

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
  echo
  echo "Bot is running. Open it in Telegram and send /start."
  echo "Logs:    journalctl -u $SERVICE -f"
  echo "Update:  run this script again"
  echo "Token:   edit $ENV_FILE, then: systemctl restart $SERVICE"
else
  echo "Bot failed to start. Recent logs:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  exit 1
fi
