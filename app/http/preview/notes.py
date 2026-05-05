"""Preview route for Obsidian note files: GET /preview/note/{vault_id}/{path:path}."""
from __future__ import annotations

from pathlib import Path

import mistune
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

router = APIRouter(tags=["preview"])

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def _render_html(title: str, content_html: str) -> str:
    tpl = _jinja_env.get_template("preview.html.j2")
    return tpl.render(title=title, content=content_html)


@router.get("/preview/note/{vault_id}/{path:path}", response_class=HTMLResponse)
async def preview_note(vault_id: str, path: str, request: Request) -> HTMLResponse:
    """Render a vault note as HTML preview."""
    container = request.app.state.container
    cfg = container.cfg

    vault_cfg = next((v for v in cfg.sources.vaults if v.id == vault_id), None)
    if vault_cfg is None:
        raise HTTPException(status_code=404, detail=f"Vault '{vault_id}' not found in config")

    # Guard against path traversal (e.g. %2e%2e%2f decoded by FastAPI)
    note_path = (vault_cfg.path / path).resolve()
    vault_root = vault_cfg.path.resolve()
    if not str(note_path).startswith(str(vault_root) + "/") and note_path != vault_root:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not note_path.exists() or not note_path.is_file():
        raise HTTPException(status_code=404, detail=f"Note '{path}' not found in vault '{vault_id}'")

    md_text = note_path.read_text(encoding="utf-8")
    content_html = mistune.html(md_text)
    title = Path(path).stem
    return HTMLResponse(_render_html(title, content_html))
