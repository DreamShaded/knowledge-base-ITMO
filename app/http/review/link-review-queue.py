"""GET /review/links — semantic link candidates review queue."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.http.page_layout import page_close, page_open
from app.logging import get_logger

router = APIRouter(tags=["review"])
log = get_logger(__name__)


@router.get("/review/links", response_class=HTMLResponse)
async def link_review_page(request: Request) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        rows = conn.execute(
            """SELECT id, vault_id, source_note_id, target_note_id, score
               FROM semantic_link_candidates
               WHERE status = 'pending'
                 AND (defer_until IS NULL OR defer_until < datetime('now'))
               ORDER BY score DESC
               LIMIT 200"""
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM semantic_link_candidates WHERE status='pending'"
        ).fetchone()[0]
    except Exception as exc:
        log.warning("link_review_query_error", error=str(exc))
        rows, total = [], 0
    finally:
        conn.close()

    return HTMLResponse(_render_page([dict(r) for r in rows], total))


def _note_label(source_id: str) -> str:
    """'personal:folder/Note Title.md' → 'Note Title'"""
    rel = source_id.split(":", 1)[1] if ":" in source_id else source_id
    return Path(unquote(rel)).stem


def _obsidian_uri(note_id: str) -> str:
    """'vault:folder/Note.md' → obsidian://open?vault=vault&file=folder%2FNote.md"""
    if ":" not in note_id:
        return ""
    vault, path = note_id.split(":", 1)
    return f"obsidian://open?vault={quote(vault)}&file={quote(unquote(path))}"


def _render_page(items: list[dict], total: int) -> str:
    rows_html = ""
    for item in items:
        iid = item["id"]
        src_label = _esc(f"{_note_label(item['source_note_id'])} ({item['vault_id']})")
        tgt_label = _esc(_note_label(item["target_note_id"]))
        src_title = _esc_attr(unquote(item["source_note_id"]))
        tgt_title = _esc_attr(unquote(item["target_note_id"]))
        src_uri = _esc_attr(_obsidian_uri(item["source_note_id"]))
        tgt_uri = _esc_attr(_obsidian_uri(item["target_note_id"]))
        score_pct = f"{item['score'] * 100:.0f}%"

        src_cell = (
            f'<a href="{src_uri}" title="{src_title}">{src_label}</a>'
            if src_uri else f'<span title="{src_title}">{src_label}</span>'
        )
        tgt_cell = (
            f'<a href="{tgt_uri}" title="{tgt_title}">{tgt_label}</a>'
            if tgt_uri else f'<span title="{tgt_title}">{tgt_label}</span>'
        )

        rows_html += (
            f'<tr id="slc-row-{iid}">'
            f'<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{src_cell}</td>'
            f'<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{tgt_cell}</td>'
            f'<td style="white-space:nowrap;color:var(--muted);font-size:.78rem">{score_pct}</td>'
            f'<td style="white-space:nowrap">'
            f'<button class="btn-action approve" hx-post="/review/links/{iid}/approve"'
            f' hx-target="#slc-row-{iid}" hx-swap="outerHTML">&#10003; Принять</button> '
            f'<button class="btn-action reject" hx-post="/review/links/{iid}/reject"'
            f' hx-target="#slc-row-{iid}" hx-swap="outerHTML">&#10007; Отклонить</button> '
            f'<button class="btn-action defer" hx-post="/review/links/{iid}/defer"'
            f' hx-target="#slc-row-{iid}" hx-swap="outerHTML">&#8987; Отложить</button>'
            f"</td></tr>"
        )

    badge = f'<span class="badge-count">{total}</span>'
    empty = '<tr><td colspan="4" class="empty-state">Нет кандидатов — запустите <code>uv run python -m app links discover</code></td></tr>'

    return (
        page_open("Семантические связи", "links")
        + f'    <div class="page-title"><h1>Семантические связи{badge}</h1>'
        + f'    <p>Ожидают ревью: {total} (показано до&nbsp;200) — '
        + '    <a href="#" style="font-size:.78rem" onclick="discover()">&#9654; Запустить поиск</a></p></div>\n'
        + '    <div class="table-card"><table>'
        + "<thead><tr>"
        + "<th>Заметка</th><th>Похожая заметка</th><th>Сходство</th><th>Действия</th>"
        + "</tr></thead>"
        + f"<tbody>{rows_html if rows_html else empty}</tbody>"
        + "</table></div>\n"
        + _discover_script()
        + page_close()
    )


def _discover_script() -> str:
    return """    <script>
    function discover() {
      fetch('/review/links/discover', {method: 'POST'})
        .then(r => r.json())
        .then(d => { alert(d.message); location.reload(); })
        .catch(() => alert('Ошибка запуска'));
      return false;
    }
    </script>\n"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
