"""Preview route for book chapters: GET /preview/book/{sha}/{chapter_id}."""
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

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _render_html(title: str, content_html: str) -> str:
    tpl = _jinja_env.get_template("preview.html.j2")
    return tpl.render(title=title, content=content_html)


@router.get("/preview/book/{sha}/{chapter_id}", response_class=HTMLResponse)
async def preview_book_chapter(sha: str, chapter_id: str, request: Request) -> HTMLResponse:
    """Render a book chapter as HTML preview."""
    # Validate sha and chapter_id to safe identifiers before path construction
    if not _SAFE_ID.match(sha) or not _SAFE_ID.match(chapter_id):
        raise HTTPException(status_code=400, detail="Invalid identifier")

    books_root = (REPO_ROOT / "data" / "books").resolve()
    chapter_path = (books_root / sha / "chapters" / chapter_id / "content.md").resolve()
    if not str(chapter_path).startswith(str(books_root) + "/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not chapter_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Chapter '{chapter_id}' not found for book '{sha}'",
        )

    md_text = chapter_path.read_text(encoding="utf-8")
    content_html = mistune.html(md_text)
    title = f"Chapter {chapter_id}"
    return HTMLResponse(_render_html(title, content_html))
