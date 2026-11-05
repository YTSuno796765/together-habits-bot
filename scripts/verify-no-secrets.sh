#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

fail=0

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  case "$file" in
    .tmp/*) continue ;;
  esac
  [[ -f "$file" ]] || continue
  base="$(basename "$file")"
  if [[ ( "$base" =~ ^\.env(\.|$) && "$base" != ".env.example" ) || "$base" == "auth.json" ]]; then
    echo "Forbidden file: $file" >&2
    fail=1
    continue
  fi
  if [[ "$file" == "./.claude/settings.local.json" ]]; then
    echo "Forbidden file: $file" >&2
    fail=1
    continue
  fi
  if [[ ! -s "$file" || $(wc -c < "$file") -gt 5242880 ]]; then
    continue
  fi
  if LC_ALL=C grep -Iq . "$file"; then
    if grep -Eq -- '-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----|^[[:space:]]*TELEGRAM_BOT_TOKEN\s*=\s*([^*[:space:]][^[:space:]]*)|[0-9]{8,12}:[A-Za-z0-9_-]{30,}|(openai|anthropic|github|todoist|notion|api)[_-]?(key|token)\s*[:=]\s*[^*[:space:]][A-Za-z0-9_.-]{15,}|(password|passwd|pwd)\s*[:=]\s*[^*[:space:]]{6,}' "$file"; then
      echo "Potential secret in $file" >&2
      fail=1
    fi
    # Public IPv4 addresses; loopback, private and documentation ranges are fine
    # (same exclusions as verify-no-secrets.ps1).
    if grep -Eo -- '([0-9]{1,3}\.){3}[0-9]{1,3}' "$file" \
      | grep -Evq '^(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.|0\.0\.0\.0$|255\.255\.255\.255$|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)'; then
      echo "Potential IP address in $file" >&2
      fail=1
    fi
  fi
done < <(git ls-files --cached --others --exclude-standard)

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "No obvious secrets found."
