#!/usr/bin/env bash
# upgrade.sh — Upgrade one or all components with smoke-test and auto-rollback.
#
# Usage:
#   ./scripts/upgrade.sh [--component=open-webui|ollama|searxng|hf-models|all]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPONENT="all"
for arg in "$@"; do
  case "$arg" in
    --component=*) COMPONENT="${arg#*=}" ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

VALID="open-webui ollama searxng hf-models all"
if ! echo "$VALID" | grep -qw "$COMPONENT"; then
  echo "Unknown component '$COMPONENT'. Valid: $VALID" >&2; exit 1
fi

YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RED='\033[0;31m'; RESET='\033[0m'
info()  { echo -e "${GREEN}[upgrade]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[upgrade]${RESET} $*"; }
fatal() { echo -e "${RED}[upgrade] FATAL:${RESET} $*" >&2; exit 1; }

PYTHON="$REPO_ROOT/.venv/bin/python3"
[[ -f "$PYTHON" ]] || fatal "venv not found — run scripts/install.sh first"

VERSIONS_FILE="$REPO_ROOT/data/.versions.json"
TS=$(date +%Y%m%d-%H%M%S)

# ── Snapshot current versions ─────────────────────────────────────────────────
if [[ -f "$VERSIONS_FILE" ]]; then
  BACKUP_FILE="$REPO_ROOT/data/.versions.${TS}.json"
  cp "$VERSIONS_FILE" "$BACKUP_FILE"
  info "Versions snapshot: $BACKUP_FILE"
fi

# ── Helper: update versions.json ──────────────────────────────────────────────
update_version() {
  local key="$1" field="$2" value="$3"
  "$PYTHON" - <<PYEOF
import json
from pathlib import Path
f = Path("$VERSIONS_FILE")
d = json.loads(f.read_text()) if f.exists() else {}
d["$key"] = {"$field": "$value"}
f.write_text(json.dumps(d, indent=2))
PYEOF
}

# ── Upgrade functions ─────────────────────────────────────────────────────────
upgrade_open_webui() {
  info "Upgrading Open WebUI..."
  OLD_VER=$(uv tool run open-webui --version 2>/dev/null || echo "unknown")
  if uv tool upgrade open-webui; then
    NEW_VER=$(uv tool run open-webui --version 2>/dev/null || echo "unknown")
    update_version "open-webui" "version" "$NEW_VER"
    info "Open WebUI: $OLD_VER → $NEW_VER"
  else
    warn "Upgrade failed — rolling back to $OLD_VER"
    uv tool install "open-webui==$OLD_VER" 2>/dev/null || warn "Rollback failed; restore manually"
    return 1
  fi
}

upgrade_ollama() {
  info "Upgrading Ollama model..."
  PROFILE="${KB_PROFILE:-$(grep '^KB_PROFILE=' .env 2>/dev/null | cut -d= -f2 || echo 'laptop-4070-8gb')}"
  case "$PROFILE" in
    laptop-4070-8gb)           MODEL="qwen3:8b"  ;;
    desktop-mid-12-16gb)       MODEL="qwen3:14b" ;;
    desktop-large-24gb-plus)   MODEL="qwen3:14b" ;;
    *)                         MODEL="qwen3:8b"  ;;
  esac
  # Ollama does not support pulling historical digests — warn only
  warn "Ollama: pulling $MODEL (rollback not possible if old digest unavailable)"
  ollama pull "$MODEL"
  update_version "ollama/$MODEL" "digest" "latest-$(date +%Y%m%d)"
  info "Ollama $MODEL pulled"
}

upgrade_searxng() {
  info "Upgrading SearXNG..."
  SEARXNG_DIR="$REPO_ROOT/vendor/searxng"
  [[ -d "$SEARXNG_DIR/.git" ]] || fatal "SearXNG not found at $SEARXNG_DIR — run install.sh first"
  OLD_SHA=$(git -C "$SEARXNG_DIR" rev-parse HEAD)
  if git -C "$SEARXNG_DIR" pull; then
    NEW_SHA=$(git -C "$SEARXNG_DIR" rev-parse HEAD)
    update_version "searxng" "sha" "$NEW_SHA"
    info "SearXNG: ${OLD_SHA:0:7} → ${NEW_SHA:0:7}"
  else
    warn "git pull failed — rolling back to $OLD_SHA"
    git -C "$SEARXNG_DIR" checkout "$OLD_SHA"
    return 1
  fi
}

upgrade_hf_models() {
  info "Upgrading HuggingFace models..."
  PROFILE="${KB_PROFILE:-$(grep '^KB_PROFILE=' .env 2>/dev/null | cut -d= -f2 || echo 'laptop-4070-8gb')}"
  case "$PROFILE" in
    laptop-4070-8gb)
      MODELS="Qwen/Qwen3-Embedding-0.6B Qwen/Qwen3-Reranker-0.6B" ;;
    *)
      MODELS="Qwen/Qwen3-Embedding-4B Qwen/Qwen3-Reranker-4B" ;;
  esac

  export HF_HOME="$REPO_ROOT/data/models"
  for repo_id in $MODELS; do
    info "  hf pull: $repo_id"
    # Load old revision for rollback
    OLD_REV=$("$PYTHON" -c "
import json; from pathlib import Path
f = Path('$VERSIONS_FILE')
d = json.loads(f.read_text()) if f.exists() else {}
print(d.get('$repo_id', {}).get('revision', 'main'))
" 2>/dev/null || echo "main")

    "$PYTHON" - <<PYEOF || { warn "  $repo_id upgrade failed — keeping cached version"; continue; }
from huggingface_hub import snapshot_download, HfApi
repo_id = "$repo_id"
snapshot_download(repo_id=repo_id, revision="main")
info = HfApi().repo_info(repo_id)
rev = info.sha or "unknown"

import json
from pathlib import Path
f = Path("$VERSIONS_FILE")
d = json.loads(f.read_text()) if f.exists() else {}
d[repo_id] = {"revision": rev}
f.write_text(json.dumps(d, indent=2))
print(f"  updated: {repo_id} → {rev[:12]}")
PYEOF
  done
}

# ── Smoke test ────────────────────────────────────────────────────────────────
smoke_test() {
  info "Running smoke test..."
  local ok=0

  # healthz
  if curl -sf http://localhost:8000/healthz > /dev/null 2>&1; then
    info "  /healthz: OK"
  else
    warn "  /healthz: FAILED (app not running?)"
    ok=1
  fi

  # readyz
  if curl -sf http://localhost:8000/readyz > /dev/null 2>&1; then
    info "  /readyz: OK"
  else
    warn "  /readyz: FAILED"
    ok=1
  fi

  # Ollama ping
  if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    info "  Ollama: OK"
  else
    warn "  Ollama: not responding"
    ok=1
  fi

  return $ok
}

# ── Run upgrades ──────────────────────────────────────────────────────────────
case "$COMPONENT" in
  open-webui) upgrade_open_webui ;;
  ollama)     upgrade_ollama ;;
  searxng)    upgrade_searxng ;;
  hf-models)  upgrade_hf_models ;;
  all)
    upgrade_open_webui || true
    upgrade_ollama     || true
    upgrade_searxng    || true
    upgrade_hf_models  || true
    ;;
esac

if ! smoke_test; then
  warn "Smoke test had failures — check logs with: journalctl --user -u kb-app -n 50"
  exit 1
fi

info "Upgrade complete. Component: $COMPONENT"
