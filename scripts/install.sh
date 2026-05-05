#!/usr/bin/env bash
# install.sh — Bootstrap the Knowledge Base system.
#
# Usage:
#   ./scripts/install.sh [--auto | --review | --manual] [--profile=<name>]
#
# Modes:
#   --auto    Profile autodetect + full install + systemd enable --now  (default if omitted but GPU present)
#   --review  Same as --auto but stops before enable --now; prints commands
#   --manual  Dependencies + data dirs only; no model downloads, no systemd
#
# Idempotent: skips already-downloaded models; regenerates systemd units.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="review"
EXPLICIT_PROFILE=""

for arg in "$@"; do
  case "$arg" in
    --auto)           MODE="auto" ;;
    --review)         MODE="review" ;;
    --manual)         MODE="manual" ;;
    --profile=*)      EXPLICIT_PROFILE="${arg#*=}" ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RED='\033[0;31m'; RESET='\033[0m'
info()  { echo -e "${GREEN}[install]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[install]${RESET} $*"; }
fatal() { echo -e "${RED}[install] FATAL:${RESET} $*" >&2; exit 1; }

# ── 1. Check uv ───────────────────────────────────────────────────────────────
command -v uv &>/dev/null || fatal "uv not found. Install from https://docs.astral.sh/uv/"

# ── 2. Detect / validate profile ──────────────────────────────────────────────
if [[ -n "$EXPLICIT_PROFILE" ]]; then
  VALID_PROFILES="laptop-4070-8gb desktop-mid-12-16gb desktop-large-24gb-plus"
  if ! echo "$VALID_PROFILES" | grep -qw "$EXPLICIT_PROFILE"; then
    fatal "Unknown profile '$EXPLICIT_PROFILE'. Valid: $VALID_PROFILES"
  fi
  PROFILE="$EXPLICIT_PROFILE"
elif [[ -n "${KB_PROFILE:-}" ]]; then
  PROFILE="$KB_PROFILE"
else
  if ! command -v nvidia-smi &>/dev/null; then
    fatal "nvidia-smi not found. MVP requires NVIDIA GPU >= 7 GB VRAM.\nCPU-only is future work — see ADR-0013 §10."
  fi
  VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  if [[ -z "$VRAM_MB" || ! "$VRAM_MB" =~ ^[0-9]+$ ]]; then
    fatal "Could not read GPU VRAM from nvidia-smi."
  fi
  if   (( VRAM_MB >= 24000 )); then PROFILE="desktop-large-24gb-plus"
  elif (( VRAM_MB >= 12000 )); then PROFILE="desktop-mid-12-16gb"
  elif (( VRAM_MB >=  7000 )); then PROFILE="laptop-4070-8gb"
  else
    fatal "GPU VRAM ${VRAM_MB} MB < 7 GB minimum.\nMVP requires NVIDIA GPU >= 7 GB VRAM. CPU-only is future work — see ADR-0013 §10."
  fi
fi

info "Profile: $PROFILE (VRAM: ${VRAM_MB:-n/a} MB)"

# ── 3. uv sync ────────────────────────────────────────────────────────────────
info "Running uv sync..."
uv sync

PYTHON="$REPO_ROOT/.venv/bin/python3"
[[ -f "$PYTHON" ]] || fatal ".venv/bin/python3 not found after uv sync"

# ── 4. Create data directories ────────────────────────────────────────────────
info "Creating data directories..."
for dir in data/qdrant data/oxigraph data/sqlite data/books data/web-saved \
           data/kge/runs data/models data/cache data/logs data/open-webui; do
  mkdir -p "$REPO_ROOT/$dir"
done

# ── 5. Write .env with KB_PROFILE ─────────────────────────────────────────────
ENV_FILE="$REPO_ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_ROOT/.env.example" "$ENV_FILE"
fi
# Update/add KB_PROFILE line
if grep -q "^KB_PROFILE=" "$ENV_FILE" 2>/dev/null; then
  sed -i "s|^KB_PROFILE=.*|KB_PROFILE=$PROFILE|" "$ENV_FILE"
else
  echo "KB_PROFILE=$PROFILE" >> "$ENV_FILE"
fi
info ".env: KB_PROFILE=$PROFILE"

if [[ "$MODE" == "manual" ]]; then
  info "Manual mode: done (deps + data dirs only)."
  exit 0
fi

# ── 6. Determine profile-specific model IDs ───────────────────────────────────
case "$PROFILE" in
  laptop-4070-8gb)
    HF_EMBED="Qwen/Qwen3-Embedding-0.6B"
    HF_RERANK="Qwen/Qwen3-Reranker-0.6B"
    OLLAMA_MODEL="qwen3:8b"
    ;;
  desktop-mid-12-16gb)
    HF_EMBED="Qwen/Qwen3-Embedding-4B"
    HF_RERANK="Qwen/Qwen3-Reranker-4B"
    OLLAMA_MODEL="qwen3:14b"
    ;;
  desktop-large-24gb-plus)
    HF_EMBED="Qwen/Qwen3-Embedding-4B"
    HF_RERANK="Qwen/Qwen3-Reranker-4B"
    OLLAMA_MODEL="qwen3:14b"
    ;;
esac

VERSIONS_FILE="$REPO_ROOT/data/.versions.json"

# ── 7. Download HF models ─────────────────────────────────────────────────────
info "Downloading HuggingFace models → data/models/ ..."
HF_HOME="$REPO_ROOT/data/models"
export HF_HOME

download_hf_model() {
  local repo_id="$1"
  info "  hf: $repo_id"
  "$PYTHON" - <<PYEOF
import json, sys
from pathlib import Path
from huggingface_hub import snapshot_download

repo_id = "$repo_id"
local_dir = snapshot_download(repo_id=repo_id, local_dir=None)
print(f"  cached: {local_dir}", flush=True)

# Record revision SHA
from huggingface_hub import HfApi
api = HfApi()
info = api.repo_info(repo_id)
rev = info.sha or "unknown"

versions_file = Path("$VERSIONS_FILE")
data = json.loads(versions_file.read_text()) if versions_file.exists() else {}
data[repo_id] = {"revision": rev}
versions_file.write_text(json.dumps(data, indent=2))
PYEOF
}

download_hf_model "$HF_EMBED"
download_hf_model "$HF_RERANK"

# ── 8. Ollama pull ────────────────────────────────────────────────────────────
info "Pulling Ollama model: $OLLAMA_MODEL ..."
if command -v ollama &>/dev/null; then
  ollama pull "$OLLAMA_MODEL"
  OLLAMA_DIGEST=$(ollama show --modelfile "$OLLAMA_MODEL" 2>/dev/null | awk '/^FROM /{print $2; exit}' || echo "unknown")
  "$PYTHON" - <<PYEOF
import json
from pathlib import Path
f = Path("$VERSIONS_FILE")
d = json.loads(f.read_text()) if f.exists() else {}
d["ollama/$OLLAMA_MODEL"] = {"digest": "$OLLAMA_DIGEST"}
f.write_text(json.dumps(d, indent=2))
PYEOF
else
  warn "ollama not found in PATH — skipping model pull. Install from https://ollama.com"
fi

# ── 9. Install Open WebUI ─────────────────────────────────────────────────────
info "Installing Open WebUI..."
if uv tool install open-webui 2>/dev/null || uv tool upgrade open-webui 2>/dev/null; then
  OWU_VERSION=$(uv tool run open-webui --version 2>/dev/null || echo "unknown")
  "$PYTHON" - <<PYEOF
import json
from pathlib import Path
f = Path("$VERSIONS_FILE")
d = json.loads(f.read_text()) if f.exists() else {}
d["open-webui"] = {"version": "$OWU_VERSION"}
f.write_text(json.dumps(d, indent=2))
PYEOF
  info "Open WebUI: $OWU_VERSION"
else
  warn "Open WebUI install failed — continuing without it"
fi

# ── 10. Clone SearXNG ─────────────────────────────────────────────────────────
SEARXNG_DIR="$REPO_ROOT/vendor/searxng"
if [[ -d "$SEARXNG_DIR/.git" ]]; then
  info "SearXNG already cloned — skipping"
else
  info "Cloning SearXNG..."
  git clone --depth=1 https://github.com/searxng/searxng.git "$SEARXNG_DIR"
fi
SEARXNG_SHA=$(git -C "$SEARXNG_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
"$PYTHON" - <<PYEOF
import json
from pathlib import Path
f = Path("$VERSIONS_FILE")
d = json.loads(f.read_text()) if f.exists() else {}
d["searxng"] = {"sha": "$SEARXNG_SHA"}
f.write_text(json.dumps(d, indent=2))
PYEOF

# ── 10b. Create SearXNG venv and install dependencies ────────────────────────
if [[ ! -x "$SEARXNG_DIR/.venv/bin/python3" ]]; then
  info "Creating SearXNG venv..."
  uv venv "$SEARXNG_DIR/.venv"
  uv pip install --python "$SEARXNG_DIR/.venv/bin/python3" \
    -r "$SEARXNG_DIR/requirements.txt"
else
  info "SearXNG venv already exists — skipping"
fi

# ── 11. Generate systemd unit files ───────────────────────────────────────────
info "Generating systemd unit files..."
ENV_PATH="$REPO_ROOT/.venv/bin:$PATH"
OPEN_WEBUI_BIN="$(uv tool dir 2>/dev/null)/open-webui/bin/open-webui"
[ -x "$OPEN_WEBUI_BIN" ] || OPEN_WEBUI_BIN="$(command -v open-webui 2>/dev/null || echo open-webui)"
SEARXNG_PYTHON="$REPO_ROOT/vendor/searxng/.venv/bin/python3"

GENERATED_DIR="$REPO_ROOT/infra/systemd/generated"
mkdir -p "$GENERATED_DIR"

"$PYTHON" - <<PYEOF
from jinja2 import Template
from pathlib import Path
import os

ctx = {
    "repo_root":       "$REPO_ROOT",
    "python_bin":      "$PYTHON",
    "python_bin_searxng": "$SEARXNG_PYTHON",
    "open_webui_bin":  "$OPEN_WEBUI_BIN",
    "env_path":        "$ENV_PATH",
    "profile":         "$PROFILE",
}

tmpl_dir = Path("$REPO_ROOT/infra/systemd")
out_dir  = Path("$GENERATED_DIR")
out_dir.mkdir(exist_ok=True)

units = {"app.service.j2": "kb-app.service",
         "searxng.service.j2": "kb-searxng.service",
         "open-webui.service.j2": "kb-open-webui.service"}

for tmpl_name, unit_name in units.items():
    tmpl_path = tmpl_dir / tmpl_name
    if not tmpl_path.exists():
        continue
    rendered = Template(tmpl_path.read_text()).render(**ctx)
    out_path = out_dir / unit_name
    out_path.write_text(rendered)
    print(f"  generated: {out_path}")
PYEOF

# ── 12. Symlink units to ~/.config/systemd/user/ ──────────────────────────────
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

for unit_file in "$GENERATED_DIR"/*.service; do
  unit_name=$(basename "$unit_file")
  target="$SYSTEMD_USER_DIR/$unit_name"
  ln -sf "$unit_file" "$target"
  info "symlinked: $target"
done

systemctl --user daemon-reload

# ── 13. Enable / print commands ───────────────────────────────────────────────
UNITS="kb-app.service kb-searxng.service kb-open-webui.service"

if [[ "$MODE" == "auto" ]]; then
  info "Enabling and starting services..."
  for u in $UNITS; do
    systemctl --user enable --now "$u" 2>/dev/null || warn "Could not enable $u — start manually"
  done
  info "Done. Services started."
  info "  App:       http://localhost:8000"
  info "  WebUI:     http://localhost:3000"
  info "  Healthz:   curl http://localhost:8000/healthz"
else
  echo ""
  warn "Review mode — run these commands to start:"
  for u in $UNITS; do
    echo "  systemctl --user enable --now $u"
  done
  echo ""
  info "Or start once without enabling:"
  for u in $UNITS; do
    echo "  systemctl --user start $u"
  done
fi

info "Install complete. Profile: $PROFILE"
info "Run 'python -m app diag profile' to verify effective config."
