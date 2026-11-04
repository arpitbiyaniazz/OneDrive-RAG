#!/usr/bin/env bash
# One-time setup of an Ubuntu server (for example an Oracle Cloud Always Free VM) for docker-compose.prod.yml.
# Run it from the repository root on the server:
#   ./deploy/server-setup.sh yourname.duckdns.org you@gmail.com,colleague@gmail.com
# It installs Docker, opens ports 80 and 443 in the server's own firewall, and creates .env from
# deploy/.env.production.example with the domain, the accounts allowed to sign in and fresh random secrets.
# It never overwrites an existing .env. Safe to run again.
set -euo pipefail

DOMAIN="${1:-}"
ALLOWED="${2:-}"
if [[ -z "$DOMAIN" || "$DOMAIN" == http* || "$DOMAIN" == */* || "$ALLOWED" =~ [[:space:]] ]]; then
  echo "Usage: $0 <domain> [allowed accounts]" >&2
  echo "  domain: a host name such as yourname.duckdns.org, without https://" >&2
  echo "  allowed accounts: comma-separated, no spaces, e.g. you@gmail.com,@yourcompany.com" >&2
  exit 1
fi
cd "$(dirname "$0")/.."
if [[ ! -f docker-compose.prod.yml ]]; then
  echo "Run this from a checkout of the repository (docker-compose.prod.yml not found)." >&2
  exit 1
fi

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  echo "    Added $USER to the docker group (takes effect at your next login; until then use sudo docker)."
fi
docker compose version >/dev/null 2>&1 || sudo docker compose version >/dev/null

echo "==> Firewall (ports 80 and 443)"
# Oracle's Ubuntu images reject incoming traffic with iptables rules besides the cloud security list.
if command -v iptables >/dev/null 2>&1; then
  for port in 80 443; do
    if ! sudo iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null; then
      sudo iptables -I INPUT 1 -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
    fi
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save >/dev/null
  fi
fi
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow 80/tcp >/dev/null
  sudo ufw allow 443/tcp >/dev/null
fi
echo "    Also allow TCP 80 and 443 in the cloud firewall (Oracle: the subnet's security list)."

echo "==> .env"
if [[ -f .env ]]; then
  echo "    .env already exists: left unchanged."
else
  command -v openssl >/dev/null 2>&1 || { sudo apt-get update -qq && sudo apt-get install -y -qq openssl >/dev/null; }
  # set_value KEY VALUE: replaces the KEY= line, keeping any comment after the value. Rewrites .env in place,
  # so it keeps its owner-only permissions.
  set_value() {
    local key="$1" value="$2" updated
    updated=$(awk -v key="$key" -v value="$value" '
      BEGIN { FS = "=" }
      $1 == key {
        rest = substr($0, length(key) + 2)
        comment = ""
        if (match(rest, /[ \t]+#.*/)) comment = substr(rest, RSTART)
        print key "=" value comment
        next
      }
      { print }
    ' .env)
    printf '%s\n' "$updated" > .env
  }
  (umask 077 && cp deploy/.env.production.example .env)
  chmod 600 .env
  set_value DOMAIN "$DOMAIN"
  set_value FRONTEND_URL "https://$DOMAIN"
  set_value CORS_ORIGINS "https://$DOMAIN"
  set_value GOOGLE_REDIRECT_URI "https://$DOMAIN/auth/google/callback"
  set_value ALLOWED_EMAILS "$ALLOWED"
  set_value SECRET_KEY "$(openssl rand -hex 32)"
  set_value TOKEN_ENCRYPTION_KEY "$(openssl rand -hex 32)"
  set_value POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  echo "    Created .env for https://$DOMAIN with new secrets."
fi

missing=$(grep -E '^(MISTRAL_API_KEY|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|ALLOWED_EMAILS)=[[:space:]]*(#.*)?$' .env | cut -d= -f1 || true)
echo
if [[ -n "$missing" ]]; then
  echo "Next: put these values in .env (nano .env):"
  echo "$missing" | sed 's/^/  - /'
  if echo "$missing" | grep -q ALLOWED_EMAILS; then
    echo "    (ALLOWED_EMAILS: the Google accounts that may sign in, e.g. you@gmail.com,@yourcompany.com)"
  fi
  echo "then start the app:"
else
  echo "Next: start the app:"
fi
echo "  docker compose -f docker-compose.prod.yml up -d --build"
echo "and open https://$DOMAIN (the first start builds the images and gets a certificate: a few minutes)."
