"""Preview route for saved web pages: GET /preview/web/{sha}."""
from __future__ import annotations

import re
from pathlib import Path

import mistune
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import REPO_ROOT

router = APIRouter(tags=["preview"])

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

_SAFE_SHA = re.compile(r"^[a-fA-F0-9]{8,64}$")


def _render_html(title: str, content_html: str) -> str:
    tpl = _jinja_env.get_template("preview.html.j2")
    return tpl.render(title=title, content=content_html)


@router.get("/preview/web/{sha}", response_class=HTMLResponse)
async def preview_web_saved(sha: str, request: Request) -> HTMLResponse:
    """Render a saved web page as HTML preview.

    Tries index.html first (served verbatim); falls back to content.md via mistune.
    """
    if not _SAFE_SHA.match(sha):
        raise HTTPException(status_code=400, detail="Invalid identifier")

    web_root = (REPO_ROOT / "data" / "web-saved").resolve()
    base_dir = (web_root / sha).resolve()
    if not str(base_dir).startswith(str(web_root) + "/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    html_path = base_dir / "index.html"

    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    for md_name in ("content.md", "page.md"):
        md_path = base_dir / md_name
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
            title = sha
            if md_text.startswith("---"):
                end = md_text.find("\n---", 3)
                if end != -1:
                    fm = md_text[3:end]
                    for line in fm.splitlines():
                        if line.startswith("title:"):
                            title = line[6:].strip().strip("'\"")
                            break
                    md_text = md_text[end + 4:].lstrip("\n")
            content_html = mistune.html(md_text)
            return HTMLResponse(_render_html(title, content_html))

    raise HTTPException(
        status_code=404,
        detail=f"Saved page '{sha}' not found (expected index.html, content.md, or page.md)",
    )
