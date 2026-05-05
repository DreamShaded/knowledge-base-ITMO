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
    except Exception as exc:
        log.warning("kge_review_query_error", error=str(exc))
        rows, total = [], 0
    finally:
        conn.close()

    return HTMLResponse(_render_kge_page([dict(r) for r in rows], total))


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

def _render_kge_page(items: list[dict], total: int) -> str:
    rows_html = ""
    for item in items:
        iid = item["id"]
        head  = _esc(item.get("head_label") or unquote(item["head_uri"].split("/")[-1]))
        rel   = _esc(item["predicted_rel"].split("#")[-1].split("/")[-1])
        tail  = _esc(item.get("tail_label") or unquote(item["tail_uri"].split("/")[-1]))
        just  = _esc(item.get("justification") or "")
        score = f"{item['score']:.3f} / z={item['zscore']:.2f}"
        rows_html += (
            f'<tr id="kge-row-{iid}">'
            f"<td>{head}</td>"
            f"<td><code>{rel}</code></td>"
            f"<td>{tail}</td>"
            f'<td style="white-space:nowrap;color:var(--muted);font-size:.78rem">{score}</td>'
            f'<td style="color:var(--muted);font-size:.78rem">{just}</td>'
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
    body = (
        page_open("KGE-предсказания", "review")
        + f'    <div class="page-title"><h1>KGE-предсказания{badge}</h1>'
        + f'    <p>Ожидают ревью: {total}</p></div>\n'
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


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
