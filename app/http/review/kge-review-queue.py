"""
GET /review       — очередь KGE-предсказаний (группировка по сущностям).
GET /review/tier1 — кандидаты Tier-1 OpenIE (перенесено из tier1_review.py).
"""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.http.page_layout import page_close, page_open
from app.logging import get_logger

router = APIRouter(tags=["review"])
log = get_logger(__name__)

_TIER1_PENDING_SPARQL = """
PREFIX pkm: <https://my-pkm.local/ontology#>
SELECT ?s ?p ?o ?conf ?chunk ?doc ?conflict WHERE {
  GRAPH <urn:tier1-pending> {
    ?s ?p ?o .
    OPTIONAL { ?s ?p ?o ; pkm:confidence ?conf }
    OPTIONAL { ?s ?p ?o ; pkm:evidenceChunk ?chunk }
    OPTIONAL { ?s ?p ?o ; pkm:evidenceDoc ?doc }
    OPTIONAL { ?s ?p ?o ; pkm:conflictFlag ?conflict }
  }
}
LIMIT 100
"""


@router.get("/review", response_class=HTMLResponse)
async def kge_review_page(request: Request) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        rows = conn.execute(
            """SELECT id, head_uri, predicted_rel, tail_uri, score, zscore,
                      head_label, tail_label, justification, entity_type, run_id
               FROM kge_review_queue
               WHERE status = 'pending'
                 AND (defer_until IS NULL OR defer_until < datetime('now'))
               ORDER BY zscore DESC
               LIMIT 100"""
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM kge_review_queue WHERE status='pending'"
        ).fetchone()[0]
        runs_count = conn.execute("SELECT COUNT(*) FROM kge_runs").fetchone()[0]
    except Exception as exc:
        log.warning("kge_review_query_error", error=str(exc))
        rows, total, runs_count = [], 0, 0
    finally:
        conn.close()

    triple_count = 0
    try:
        count_rows = container.oxigraph.sparql_select(
            "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <urn:tier0> { ?s ?p ?o } }"
        )
        triple_count = int(count_rows[0].get("n", "0") or "0") if count_rows else 0
    except Exception as exc:
        log.debug("kge_triple_count_error", error=str(exc))

    return HTMLResponse(_render_kge_page([dict(r) for r in rows], total, runs_count, triple_count))


@router.get("/review/tier1", response_class=HTMLResponse)
async def tier1_review_page(request: Request) -> HTMLResponse:
    oxigraph = request.app.state.container.oxigraph
    try:
        rows = oxigraph.sparql_select(_TIER1_PENDING_SPARQL)
        count_rows = oxigraph.sparql_select(
            "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <urn:tier1-pending> { ?s ?p ?o } }"
        )
        total = int(count_rows[0].get("n", "0") or "0") if count_rows else 0
    except Exception as exc:
        log.warning("tier1_review_sparql_error", error=str(exc))
        rows, total = [], 0

    return HTMLResponse(_render_tier1_page(_build_tier1_items(rows), total))


# ── HTML renderers ─────────────────────────────────────────────────────────────

def _render_kge_page(items: list[dict], total: int, runs_count: int, triple_count: int) -> str:
    rows_html = ""
    for item in items:
        iid = item["id"]
        head_label = _esc(_label_from_uri(item.get("head_label") or item["head_uri"]))
        head_title = _esc_attr(unquote(item["head_uri"]))
        rel        = _esc(item["predicted_rel"].split("#")[-1].split("/")[-1])
        tail_label = _esc(_label_from_uri(item.get("tail_label") or item["tail_uri"]))
        tail_title = _esc_attr(unquote(item["tail_uri"]))
        just_text  = _shorten_justification(item.get("justification") or "")
        just       = _esc(just_text)
        score      = f"{item['score']:.3f} / z={item['zscore']:.2f}"
        rows_html += (
            f'<tr id="kge-row-{iid}">'
            f'<td title="{head_title}" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{head_label}</td>'
            f"<td><code>{rel}</code></td>"
            f'<td title="{tail_title}">{tail_label}</td>'
            f'<td style="white-space:nowrap;color:var(--muted);font-size:.78rem">{score}</td>'
            f'<td title="{_esc_attr(just_text)}" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:.78rem">{just}</td>'
            f'<td style="white-space:nowrap">'
            f'<button class="btn-action approve" hx-post="/review/{iid}/approve"'
            f' hx-target="#kge-row-{iid}" hx-swap="outerHTML">&#10003; Принять</button> '
            f'<button class="btn-action reject" hx-post="/review/{iid}/reject"'
            f' hx-target="#kge-row-{iid}" hx-swap="outerHTML">&#10007; Отклонить</button> '
            f'<button class="btn-action defer" hx-post="/review/{iid}/defer"'
            f' hx-target="#kge-row-{iid}" hx-swap="outerHTML">&#8987; Отложить</button>'
            f"</td></tr>"
        )

    badge = f'<span class="badge-count">{total}</span>'
    empty = f'<tr><td colspan="6" class="empty-state">Нет ожидающих предсказаний</td></tr>'

    diag_html = _render_kge_diagnostics(runs_count, triple_count) if not items else ""

    body = (
        page_open("KGE-предсказания", "review")
        + f'    <div class="page-title"><h1>KGE-предсказания{badge}</h1>'
        + f'    <p>Ожидают ревью: {total}</p></div>\n'
        + diag_html
        + '    <div class="table-card"><table>'
        + "<thead><tr>"
        + "<th>Голова</th><th>Отношение</th><th>Хвост</th>"
        + "<th>Оценка/Z</th><th>Обоснование</th><th>Действия</th>"
        + "</tr></thead>"
        + f"<tbody>{rows_html if rows_html else empty}</tbody>"
        + "</table></div>\n"
        + page_close()
    )
    return body


def _render_kge_diagnostics(runs_count: int, triple_count: int) -> str:
    if triple_count == 0:
        status_icon = "&#9888;"
        status_color = "#d97706"
        status_text = "Граф пуст — необходима индексация"
        detail = (
            "В <code>&lt;urn:tier0&gt;</code> нет троек. "
            "Запустите <b>Сканировать источники</b> на "
            '<a href="/jobs">странице задач</a> и дождитесь завершения всех <code>index_note</code>/<code>parse_book</code> задач.'
        )
    elif runs_count == 0:
        status_icon = "&#9432;"
        status_color = "#2563eb"
        status_text = f"Граф содержит {triple_count:,} троек, модель не обучена"
        detail = (
            "Запустите обучение командой: "
            "<code>uv run python -m app kge train</code>"
        )
    else:
        status_icon = "&#10003;"
        status_color = "#16a34a"
        status_text = f"Моделей: {runs_count}, троек в графе: {triple_count:,}"
        detail = "Предсказания появятся после следующего цикла сёрфинга."

    return (
        f'    <div style="margin-bottom:1rem;padding:12px 16px;border-radius:8px;'
        f'border:1px solid #e2e8f0;background:#fff;font-size:.82rem;'
        f'display:flex;align-items:flex-start;gap:10px">'
        f'<span style="font-size:1.1rem;color:{status_color}">{status_icon}</span>'
        f'<div><b style="color:{status_color}">{status_text}</b>'
        f'<div style="color:#64748b;margin-top:3px">{detail}</div></div>'
        f'    </div>\n'
    )


def _build_tier1_items(rows: list[dict]) -> list[dict]:
    import hashlib
    seen: set[str] = set()
    items = []
    for row in rows:
        s, p, o = row.get("s", ""), row.get("p", ""), row.get("o", "")
        if not s or not p or not o:
            continue
        key = f"{s}|{p}|{o}"
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "hash": hashlib.sha256(key.encode()).hexdigest(),
            "subject": s, "predicate": p, "object": o,
            "confidence": row.get("conf", "?"),
            "conflict": row.get("conflict", "false") == "true",
        })
    return items


def _render_tier1_page(items: list[dict], total: int) -> str:
    rows_html = ""
    for item in items:
        conflict = ' <span class="badge-conflict">конфликт</span>' if item["conflict"] else ""
        subj = _esc(unquote(item["subject"].split("/")[-1]))
        pred = _esc(item["predicate"].split("#")[-1])
        obj_ = _esc(unquote(item["object"].split("/")[-1]))
        h    = _esc(item["hash"])
        su   = _esc_attr(item["subject"])
        pu   = _esc_attr(item["predicate"])
        ou   = _esc_attr(item["object"])
        rows_html += (
            "<tr>"
            f'<td style="font-size:.8em">{subj}</td>'
            f"<td><code>{pred}</code></td>"
            f'<td style="font-size:.8em">{obj_}</td>'
            f"<td>{item['confidence']}{conflict}</td>"
            f'<td style="white-space:nowrap">'
            f'<form method="post" action="/review/tier1/approve" style="display:inline">'
            f'<input type="hidden" name="triple_hash" value="{h}">'
            f'<input type="hidden" name="subject_uri" value="{su}">'
            f'<input type="hidden" name="predicate" value="{pu}">'
            f'<input type="hidden" name="object_value" value="{ou}">'
            f'<button type="submit" class="btn-action approve">&#10003; Принять</button>'
            f"</form> "
            f'<form method="post" action="/review/tier1/reject" style="display:inline">'
            f'<input type="hidden" name="triple_hash" value="{h}">'
            f'<input type="hidden" name="subject_uri" value="{su}">'
            f'<input type="hidden" name="predicate" value="{pu}">'
            f'<input type="hidden" name="object_value" value="{ou}">'
            f'<button type="submit" class="btn-action reject">&#10007; Отклонить</button>'
            f"</form> "
            f'<form method="post" action="/review/tier1/defer" style="display:inline">'
            f'<input type="hidden" name="triple_hash" value="{h}">'
            f'<button type="submit" class="btn-action defer">&#8987; Отложить</button>'
            f"</form>"
            "</td></tr>"
        )

    badge = f'<span class="badge-count">{total}</span>'
    empty = f'<tr><td colspan="5" class="empty-state">Нет ожидающих кандидатов</td></tr>'
    return (
        page_open("Кандидаты уровня 1", "tier1")
        + f'    <div class="page-title"><h1>Кандидаты уровня&nbsp;1{badge}</h1>'
        + f'    <p>Всего ожидает: {total} (показано до&nbsp;100)</p></div>\n'
        + '    <div class="table-card"><table>'
        + "<thead><tr>"
        + "<th>Субъект</th><th>Предикат</th><th>Объект</th><th>Уверенность</th><th>Действия</th>"
        + "</tr></thead>"
        + f"<tbody>{rows_html if rows_html else empty}</tbody>"
        + "</table></div>\n"
        + page_close()
    )


def _label_from_uri(uri: str) -> str:
    """Extract human-readable label from URN/URI."""
    import re as _re
    decoded = unquote(uri)
    if decoded.startswith("urn:heading:"):
        parts = decoded.split(":")
        # Find last part ending in .md → use it as filename + section suffix
        for i in range(len(parts) - 1, 1, -1):
            if parts[i].endswith(".md"):
                name = parts[i][:-3]
                section = parts[i + 1] if i + 1 < len(parts) else ""
                return f"{name} §{section}" if section else name
        return ":".join(parts[3:]) if len(parts) > 3 else decoded
    if "/" in decoded:
        return decoded.rstrip("/").split("/")[-1]
    return decoded.split(":")[-1]


def _shorten_justification(text: str) -> str:
    """Replace long URNs in justification text with readable labels."""
    import re as _re
    return _re.sub(r"urn:[^\s,]+", lambda m: _label_from_uri(m.group(0)), text)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
