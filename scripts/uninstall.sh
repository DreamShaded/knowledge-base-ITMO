#!/usr/bin/env bash
# uninstall.sh — Remove Knowledge Base services and optionally data.
#
# Usage:
#   ./scripts/uninstall.sh [--purge-data]
#
# --purge-data: also removes data/ directory (models, stores, logs, books).
#               Irreversible — prompts for confirmation.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PURGE_DATA=false
for arg in "$@"; do
  case "$arg" in
    --purge-data) PURGE_DATA=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info() { echo -e "${GREEN}[uninstall]${RESET} $*"; }
warn() { echo -e "${YELLOW}[uninstall]${RESET} $*"; }

UNITS="kb-app.service kb-searxng.service kb-open-webui.service"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

# ── Stop and disable services ─────────────────────────────────────────────────
for unit in $UNITS; do
  if systemctl --user is-active "$unit" &>/dev/null; then
    info "Stopping $unit..."
    systemctl --user stop "$unit" 2>/dev/null || true
  fi
  if systemctl --user is-enabled "$unit" &>/dev/null; then
    info "Disabling $unit..."
    systemctl --user disable "$unit" 2>/dev/null || true
  fi
done

# ── Remove symlinks from ~/.config/systemd/user/ ──────────────────────────────
for unit in $UNITS; do
  link="$SYSTEMD_USER_DIR/$unit"
  if [[ -L "$link" ]]; then
    rm -f "$link"
    info "Removed symlink: $link"
  fi
done

systemctl --user daemon-reload 2>/dev/null || true

# ── Optionally purge data/ ────────────────────────────────────────────────────
if $PURGE_DATA; then
  echo ""
  warn "WARNING: --purge-data will DELETE data/ including all indexes, models, and books."
  read -r -p "Type 'yes' to confirm permanent deletion: " confirm
  if [[ "$confirm" == "yes" ]]; then
    rm -rf "$REPO_ROOT/data"
    info "data/ removed."
  else
    info "Skipping data removal."
  fi
fi

info "Uninstall complete. Repository and .venv remain intact."
info "To reinstall: ./scripts/install.sh --review"
