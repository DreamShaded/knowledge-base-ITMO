#!/usr/bin/env bash
# Restart kb-app service and tail logs until startup complete.
set -euo pipefail

systemctl --user restart kb-app.service
echo "Restarting kb-app..."

# Wait for startup complete or failure (max 60s)
timeout 60 journalctl --user -u kb-app.service -f --no-pager \
  | while IFS= read -r line; do
      echo "$line"
      if echo "$line" | grep -q "Application startup complete"; then
          pkill -P $$ journalctl 2>/dev/null || true
          exit 0
      fi
      if echo "$line" | grep -q '"level": "error"'; then
          echo "ERROR detected during startup"
          pkill -P $$ journalctl 2>/dev/null || true
          exit 1
      fi
    done
