#!/usr/bin/env bash
# Put Aegis online on a fresh EU server. Run it ON the server, inside the repo:
#
#   git clone <repo> && cd <repo> && bash scripts/deploy-hosted.sh
#
# It asks for two domain names and an email, writes .env with fresh secrets,
# starts the stack and prints where to go. Safe to run again: an existing .env is
# kept, so a redeploy never rotates your keys.
#
# Prepare first (the only parts a script cannot do for you):
#   1. a server with Docker and Docker Compose v2 (see docs/DEPLOY_HOSTED.md)
#   2. two DNS A records -> this server's IP: app.<yourdomain>, gateway.<yourdomain>
#   3. ports 80 and 443 open
set -euo pipefail

cd "$(dirname "$0")/.."

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing: $1. $2" >&2
    exit 1
  }
}
need docker "Install Docker: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || {
  echo "Missing: docker compose (v2). https://docs.docker.com/compose/install/" >&2
  exit 1
}
need openssl "Install it (apt-get install openssl)."

secret() { openssl rand -base64 33 | tr '+/' '-_' | tr -d '=\n'; }

if [ ! -f .env ]; then
  APP_DOMAIN="${APP_DOMAIN:-}"
  GATEWAY_DOMAIN="${GATEWAY_DOMAIN:-}"
  ACME_EMAIL="${ACME_EMAIL:-}"
  [ -n "$APP_DOMAIN" ] || read -r -p "Dashboard domain (e.g. app.yourdomain.it): " APP_DOMAIN
  [ -n "$GATEWAY_DOMAIN" ] || read -r -p "Gateway domain   (e.g. gateway.yourdomain.it): " GATEWAY_DOMAIN
  [ -n "$ACME_EMAIL" ] || read -r -p "Email for the TLS certificate: " ACME_EMAIL
  if [ -z "$APP_DOMAIN" ] || [ -z "$GATEWAY_DOMAIN" ] || [ -z "$ACME_EMAIL" ]; then
    echo "All three values are required." >&2
    exit 1
  fi
  umask 077
  cat > .env <<ENV
# Written by scripts/deploy-hosted.sh. Never commit this file (it is gitignored).
APP_DOMAIN=$APP_DOMAIN
GATEWAY_DOMAIN=$GATEWAY_DOMAIN
ACME_EMAIL=$ACME_EMAIL
AEGIS_SECRET_KEY=$(secret)
AEGIS_EAT_KEY=$(secret)
AEGIS_EVIDENCE_SECRET_KEY=$(secret)
AEGIS_INVITE_CODES=$(openssl rand -hex 6)
# Approval emails: fill in to be notified. Without a host nothing is sent.
AEGIS_SMTP_HOST=
AEGIS_SMTP_PORT=587
AEGIS_SMTP_USER=
AEGIS_SMTP_PASSWORD=
AEGIS_SMTP_FROM=
ENV
  echo "Wrote .env with fresh secrets. Keep a copy somewhere safe: without them the data cannot be verified."
fi

docker compose -f docker-compose.hosted.yml up -d --build

APP_DOMAIN="$(grep '^APP_DOMAIN=' .env | cut -d= -f2-)"
GATEWAY_DOMAIN="$(grep '^GATEWAY_DOMAIN=' .env | cut -d= -f2-)"
INVITE="$(grep '^AEGIS_INVITE_CODES=' .env | cut -d= -f2- | cut -d, -f1)"

cat <<DONE

Aegis is starting. The first certificate takes a minute.

  Dashboard:  https://$APP_DOMAIN
  Gateway:    https://$GATEWAY_DOMAIN   (the address your agents call)
  Invite code to create the first organization:  $INVITE

Next: open the dashboard, choose "Create an organization", use the invite code,
then add your first agent. Status:  docker compose -f docker-compose.hosted.yml ps
Logs:    docker compose -f docker-compose.hosted.yml logs -f control-plane
DONE
