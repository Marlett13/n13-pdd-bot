#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR=/opt/n13-pdd
REPO=https://github.com/Marlett13/n13-pdd-bot.git

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root in the VPS console." >&2
  exit 1
fi
for cmd in git docker; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd. Stopping without installing or changing system packages." >&2; exit 1; }
done
docker compose version >/dev/null || { echo "Docker Compose v2 not installed." >&2; exit 1; }
if [ -e "$APP_DIR" ] && [ ! -d "$APP_DIR/.git" ]; then
  echo "$APP_DIR already exists and is not a Git checkout; refusing to overwrite." >&2
  exit 1
fi
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin main
  git -C "$APP_DIR" merge --ff-only origin/main
else
  git clone --branch main --depth 1 "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
if [ ! -s .env ]; then
  echo 'Telegram bot token required once for first installation.'
  echo 'Paste the token into this protected VPS console. It will not echo.'
  if [ ! -r /dev/tty ]; then
    echo 'Interactive terminal unavailable. Re-run this script in the VPS console.' >&2
    exit 1
  fi
  read -r -s -p 'Bot token: ' PDD_TOKEN </dev/tty
  echo
  if [[ ! "$PDD_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
    echo 'Token format invalid; nothing written.' >&2
    exit 1
  fi
  umask 077
  printf 'PDD_BOT_TOKEN=%s\n' "$PDD_TOKEN" > .env
  unset PDD_TOKEN
  chmod 600 .env
fi
docker compose -p n13-pdd -f compose.yml up -d --build
docker compose -p n13-pdd -f compose.yml ps
echo 'N13 PDD container command submitted. Check logs: docker compose -p n13-pdd logs --tail=40 pdd'
