#!/bin/zsh
# Install the monthly refresh as a launchd agent (1st of each month, 06:00 local).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.tokenrouter.refresh.plist"
sed "s|__ROOT__|$ROOT|g" "$ROOT/deploy/com.tokenrouter.refresh.plist" > "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "Installed $DEST"
