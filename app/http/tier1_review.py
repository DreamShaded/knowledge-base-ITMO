"""
/review/tier1 — Tier-1 candidates review UI (phase-10).

Routes relocated to /review/tier1/* in phase-12 (KGE predictions take /review).

GET  /review/tier1        → HTML page with pending Tier-1 candidates
POST /review/tier1/approve → move triple from tier1-pending to tier1
POST /review/tier1/reject  → move triple to tier1-rejected + log reason
POST /review/tier1/defer   → defer triple for 7 days
POST /v1/tier1/reload      → re-run OpenIE on whole vault (no re-embedding)
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.http.page_layout import page_close, page_open
from app.logging import get_logger

router = APIRouter(tags=["tier1-review"])
log = get_logger(__name__)

_PKM = "https://my-pkm.local/ontology#"
_TIER1 = "urn:tier1"
_TIER1_PENDING = "urn:tier1-pending"
_TIER1_REJECTED = "urn:tier1-rejected"

# SPARQL to list pending triples with their evidence metadata
_PENDING_SPARQL = """
PREFIX pkm: <https://my-pkm.local/ontology#>
SELECT ?s ?p ?o ?conf ?chunk ?doc ?conflict WHERE {
  GRAPH <urn:tier1-pending> {
    ?s ?p ?o .
    OPTIONAL { ?s ?p ?o ; pkm:confidence ?conf }
    OPTIONAL { ?s ?p ?o ; pkm:evidenceChunk ?chunk }
    OPTIONAL { ?s ?p ?o ; pkm:evidenceDoc ?doc }
    OPTIONAL { ?s ?p ?o ; pkm:conflictFlag ?conflict }
  }
  FILTER NOT EXISTS {
    ?s a ?type .  # skip RDF meta-triples about meta-triples
  }
}
LIMIT 100
"""

_COUNT_SPARQL = """
SELECT (COUNT(*) AS ?n) WHERE {
  GRAPH <urn:tier1-pending> { ?s ?p ?o }
}
"""


@router.get("/review/tier1", response_class=HTMLResponse)
async def review_page(request: Request, page: int = 0) -> HTMLResponse:
    container = request.app.state.container
    oxigraph = container.oxigraph

    try:
        rows = oxigraph.sparql_select(_PENDING_SPARQL)
        count_rows = oxigraph.sparql_select(_COUNT_SPARQL)
        total = int(count_rows[0].get("n", "0") or "0") if count_rows else 0
    except Exception as exc:
        log.warning("review_sparql_error", error=str(exc))
        rows = []
        total = 0

    items = _build_items(rows)
    html = _render_page(items, total, page)
    return HTMLResponse(html)


@router.post("/review/tier1/approve")
async def approve_triple(
    request: Request,
    triple_hash: str = Form(...),
    subject_uri: str = Form(...),
    predicate: str = Form(...),
    object_value: str = Form(...),
    is_literal: int = Form(0),
) -> JSONResponse:
    container = request.app.state.container
    oxigraph = container.oxigraph

    obj_part = f'"{_esc(object_value)}"' if is_literal else f"<{object_value}>"

    # Move from pending → tier1
    try:
        oxigraph.sparql_update(f"""
            DELETE {{ GRAPH <{_TIER1_PENDING}> {{
                <{subject_uri}> <{predicate}> {obj_part} .
            }} }}
            INSERT {{ GRAPH <{_TIER1}> {{
                <{subject_uri}> <{predicate}> {obj_part} .
            }} }}
            WHERE {{
                GRAPH <{_TIER1_PENDING}> {{
                    <{subject_uri}> <{predicate}> {obj_part} .
                }}
            }}
        """)
        _update_review_record(container.sqlite, triple_hash, "approved")
        log.info("tier1_approved", subj=subject_uri, pred=predicate)
        return JSONResponse({"status": "approved"})
    except Exception as exc:
        log.error("approve_failed", error=str(exc))
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


@router.post("/review/tier1/reject")
async def reject_triple(
    request: Request,
    triple_hash: str = Form(...),
    subject_uri: str = Form(...),
    predicate: str = Form(...),
    object_value: str = Form(...),
    is_literal: int = Form(0),
    reason: str = Form(""),
) -> JSONResponse:
    container = request.app.state.container
    oxigraph = container.oxigraph

    obj_part = f'"{_esc(object_value)}"' if is_literal else f"<{object_value}>"

    try:
        oxigraph.sparql_update(f"""
            DELETE {{ GRAPH <{_TIER1_PENDING}> {{
                <{subject_uri}> <{predicate}> {obj_part} .
            }} }}
            INSERT {{ GRAPH <{_TIER1_REJECTED}> {{
                <{subject_uri}> <{predicate}> {obj_part} .
            }} }}
            WHERE {{
                GRAPH <{_TIER1_PENDING}> {{
                    <{subject_uri}> <{predicate}> {obj_part} .
                }}
            }}
        """)
        _update_review_record(container.sqlite, triple_hash, "rejected", reject_reason=reason)
        log.info("tier1_rejected", subj=subject_uri, pred=predicate, reason=reason)
        return JSONResponse({"status": "rejected"})
    except Exception as exc:
        log.error("reject_failed", error=str(exc))
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


@router.post("/review/tier1/defer")
async def defer_triple(
    request: Request,
    triple_hash: str = Form(...),
) -> JSONResponse:
    """Defer triple for 7 days (it remains in tier1-pending)."""
    from datetime import datetime, timedelta, timezone

    container = request.app.state.container
    defer_until = (
        datetime.now(tz=timezone.utc) + timedelta(days=7)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    _update_review_record(
        container.sqlite, triple_hash, "deferred", defer_until=defer_until
    )
    log.info("tier1_deferred", triple_hash=triple_hash[:12], until=defer_until)
    return JSONResponse({"status": "deferred", "defer_until": defer_until})


@router.post("/v1/tier1/reload")
async def reload_openie(request: Request) -> JSONResponse:
    """Re-enqueue OpenIE for all indexed chunks (no re-embedding)."""
    from app.jobs import types as T

    container = request.app.state.container
    qdrant = container.qdrant
    dispatcher = container.dispatcher
    cfg = container.cfg
    ollama_host = cfg.ollama.host

    try:
        client = qdrant.client
        total = 0
        offset = None
        while True:
            results, next_offset = client.scroll(
                collection_name=qdrant.collection_name,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in results:
                payload = point.payload or {}
                chunk_text = payload.get("text", "")
                doc_uri = payload.get("doc_uri", "")
                chunk_id = str(point.id)
                if not chunk_text:
                    continue
                await dispatcher.enqueue(
                    job_type=T.OPENIE_CANDIDATE,
                    payload={
                        "chunk_id": chunk_id,
                        "chunk_text": chunk_text,
                        "doc_uri": doc_uri,
                        "ollama_host": ollama_host,
                    },
                    priority=1,
                    idempotency_key=f"openie_reload:{chunk_id}",
                )
                total += 1

            offset = next_offset
            if offset is None:
                break

        log.info("tier1_reload_enqueued", total=total)
        return JSONResponse({"status": "enqueued", "chunks": total})
    except Exception as exc:
        log.error("reload_failed", error=str(exc))
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_items(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    items = []
    for row in rows:
        s = row.get("s", "")
        p = row.get("p", "")
        o = row.get("o", "")
        if not s or not p or not o:
            continue
        key = f"{s}|{p}|{o}"
        if key in seen:
            continue
        seen.add(key)
        triple_hash = hashlib.sha256(key.encode()).hexdigest()
        items.append({
            "hash": triple_hash,
            "subject": s,
            "predicate": _short_uri(p),
            "predicate_uri": p,
            "object": o,
            "confidence": row.get("conf", "?"),
            "chunk": row.get("chunk", ""),
            "doc": row.get("doc", ""),
            "conflict": row.get("conflict", "false") == "true",
        })
    return items


def _short_uri(uri: str) -> str:
    part = uri.split("#")[-1] if "#" in uri else uri.split("/")[-1]
    return part


def _render_page(items: list[dict], total: int, page: int) -> str:
    rows_html = ""
    for item in items:
        conflict = ' <span class="badge-conflict">конфликт</span>' if item["conflict"] else ""
        h  = _esc_attr(item["hash"])
        su = _esc_attr(item["subject"])
        pu = _esc_attr(item["predicate_uri"])
        ou = _esc_attr(item["object"])
        rows_html += (
            "<tr>"
            f'<td style="font-size:.8em">{_esc_html(_short_uri(item["subject"]))}</td>'
            f"<td><code>{_esc_html(item['predicate'])}</code></td>"
            f'<td style="font-size:.8em">{_esc_html(_short_uri(item["object"]))}</td>'
            f"<td>{_esc_html(str(item['confidence']))}{conflict}</td>"
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
    empty = '<tr><td colspan="5" class="empty-state">Нет ожидающих кандидатов</td></tr>'
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


def _update_review_record(
    sqlite,
    triple_hash: str,
    status: str,
    reject_reason: str = "",
    defer_until: str | None = None,
) -> None:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite.connect()
    try:
        conn.execute(
            """
            UPDATE tier1_review
            SET status=?, decided_at=?, reject_reason=?, defer_until=?
            WHERE triple_hash=?
            """,
            (status, now, reject_reason or None, defer_until, triple_hash),
        )
        conn.commit()
    except Exception as exc:
        log.warning("review_record_update_failed", error=str(exc))
    finally:
        conn.close()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
