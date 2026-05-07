#!/usr/bin/env bash
# Запуск демо-инстанса KB с чистыми данными и отдельными источниками.
# Книги:  /home/r/demo-kb/books/
# Заметки: /home/r/demo-kb/notes/
set -euo pipefail

DEMO_ROOT=/home/r/demo-kb
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

exec env \
  KB_SOURCES__VAULTS="[{\"id\":\"demo\",\"label\":\"Demo Notes\",\"path\":\"$DEMO_ROOT/notes\",\"obsidian_uri_name\":\"demo\"}]" \
  KB_SOURCES__BOOK_PATHS="[\"$DEMO_ROOT/books\"]" \
  KB_QDRANT__PATH="$DEMO_ROOT/data/qdrant" \
  KB_OXIGRAPH__PATH="$DEMO_ROOT/data/oxigraph" \
  KB_SQLITE__PATH="$DEMO_ROOT/data/sqlite/app.db" \
  uv run python -m app run "$@"
