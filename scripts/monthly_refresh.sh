#!/bin/zsh
# Monthly tier refresh, run by launchd (deploy/com.tokenrouter.refresh.plist).
# Keys come from ~/.config/tokenrouter/env (chmod 600) if present, since launchd has no
# shell env; otherwise the CLI reads the project's .env itself.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${TOKENROUTER_ENV_FILE:-$HOME/.config/tokenrouter/env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi
mkdir -p data/logs
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  .venv/bin/tokenrouter audit
  .venv/bin/tokenrouter refresh
} >> data/logs/refresh.log 2>&1
