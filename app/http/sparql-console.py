"""
/sparql — консоль SPARQL (только чтение).

GET  /sparql          → HTML-форма с textarea для запроса
POST /sparql/query    → выполнить SELECT/ASK через /v1/sparql, вернуть HTML-фрагмент
"""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.http.page_layout import page_close, page_open
from app.logging import get_logger

router = APIRouter(tags=["sparql-console"])
log = get_logger(__name__)

_DEFAULT_QUERY = """\
SELECT ?s ?p ?o WHERE {
  GRAPH <urn:tier0> { ?s ?p ?o }
}
LIMIT 20"""

# {default_query} is the only placeholder — no CSS here, so no brace escaping needed.
_FORM_BODY = """\
    <div class="page-title">
      <h1>SPARQL-консоль</h1>
      <p>Только SELECT / ASK / DESCRIBE (режим чтения)</p>
    </div>
    <div class="query-card">
      <form hx-post="/sparql/query" hx-target="#results" hx-swap="innerHTML" hx-indicator="this">
        <textarea name="query">{default_query}</textarea>
        <button type="submit" class="run-btn">&#9654; Выполнить</button>
      </form>
      <div id="results" style="margin-top:1.25rem">
        <p style="color:var(--muted);font-size:.85em">Результаты появятся здесь.</p>
      </div>
    </div>
"""


@router.get("/sparql", response_class=HTMLResponse)
async def sparql_console() -> HTMLResponse:
    body = _FORM_BODY.format(default_query=_esc(_DEFAULT_QUERY))
    return HTMLResponse(page_open("SPARQL-консоль", "sparql") + body + page_close())


@router.post("/sparql/query", response_class=HTMLResponse)
async def sparql_query(request: Request, query: str = Form("")) -> HTMLResponse:
    if not query.strip():
        return HTMLResponse('<p class="error-msg">Пустой запрос.</p>')

    upper = query.strip().upper()
    if not any(upper.startswith(kw) for kw in ("SELECT", "ASK", "DESCRIBE", "PREFIX", "BASE")):
        return HTMLResponse(
            '<p class="error-msg">Разрешены только SELECT / ASK / DESCRIBE.</p>'
        )

    store = request.app.state.container.oxigraph
    try:
        rows = store.sparql_select(query)
    except Exception as exc:
        log.warning("sparql_console_error", error=str(exc))
        return HTMLResponse(f'<p class="error-msg">Ошибка: {_esc(str(exc))}</p>')

    return HTMLResponse(_render_table(rows))


def _render_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="empty-state">Нет результатов.</p>'

    headers = list(rows[0].keys())
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{_esc(unquote(str(row.get(h, ''))))}</td>" for h in headers)
        + "</tr>"
        for row in rows[:1000]
    )
    note = (
        f'<p class="result-note">{len(rows)} строк возвращено'
        + (" (лимит 1000)" if len(rows) >= 1000 else "")
        + ".</p>"
    )
    return (
        '<div class="table-card" style="margin-top:1rem">'
        f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"
        f"</div>{note}"
    )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
