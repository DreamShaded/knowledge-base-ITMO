#!/usr/bin/env bash
# Запуск основного инстанса KB (конфиг из infra/config.yaml + .env).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

exec uv run python -m app run "$@"
