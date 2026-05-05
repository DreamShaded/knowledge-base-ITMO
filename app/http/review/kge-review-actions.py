"""
KGE prediction review actions (phase-12).

POST /review/{kge_id}/approve  → prediction triple → <urn:tier0> + mark approved
POST /review/{kge_id}/reject   → mark rejected with optional reason
POST /review/{kge_id}/defer    → re-show after 7 days
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.logging import get_logger

router = APIRouter(tags=["review"])
log = get_logger(__name__)


@router.post("/review/{kge_id}/approve", response_class=HTMLResponse)
async def approve_prediction(request: Request, kge_id: int) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        row = conn.execute(
            "SELECT head_uri, predicted_rel, tail_uri FROM kge_review_queue WHERE id=?",
            (kge_id,),
        ).fetchone()
        if not row:
            return HTMLResponse(_row_feedback(kge_id, "not_found"), status_code=404)

        head_uri, rel_uri, tail_uri = row[0], row[1], row[2]
        now = _now_str()

        # Write approved triple to tier0 (explicit user action)
        container.oxigraph.sparql_update(f"""
            INSERT DATA {{
                GRAPH <urn:tier0> {{
                    <{head_uri}> <{rel_uri}> <{tail_uri}> .
                }}
            }}
        """)

        conn.execute(
            "UPDATE kge_review_queue SET status='approved', reviewed_at=? WHERE id=?",
            (now, kge_id),
        )
        conn.commit()
        log.info("kge_approved", kge_id=kge_id, head=head_uri, rel=rel_uri, tail=tail_uri)
        return HTMLResponse(_row_feedback(kge_id, "approved ✓"))
    except Exception as exc:
        log.error("kge_approve_failed", kge_id=kge_id, error=str(exc))
        return HTMLResponse(_row_feedback(kge_id, f"error: {exc}"), status_code=500)
    finally:
        conn.close()


@router.post("/review/{kge_id}/reject", response_class=HTMLResponse)
async def reject_prediction(
    request: Request,
    kge_id: int,
    reason: str = Form(""),
) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        now = _now_str()
        conn.execute(
            "UPDATE kge_review_queue SET status='rejected', reviewed_at=?, reject_reason=? WHERE id=?",
            (now, reason or None, kge_id),
        )
        conn.commit()
        log.info("kge_rejected", kge_id=kge_id, reason=reason)
        return HTMLResponse(_row_feedback(kge_id, "rejected ✗"))
    except Exception as exc:
        log.error("kge_reject_failed", kge_id=kge_id, error=str(exc))
        return HTMLResponse(_row_feedback(kge_id, f"error: {exc}"), status_code=500)
    finally:
        conn.close()


@router.post("/review/{kge_id}/defer", response_class=HTMLResponse)
async def defer_prediction(request: Request, kge_id: int) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        defer_until = (
            datetime.now(tz=timezone.utc) + timedelta(days=7)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE kge_review_queue SET status='deferred', defer_until=? WHERE id=?",
            (defer_until, kge_id),
        )
        conn.commit()
        log.info("kge_deferred", kge_id=kge_id, until=defer_until)
        return HTMLResponse(_row_feedback(kge_id, "deferred ⏱"))
    except Exception as exc:
        log.error("kge_defer_failed", kge_id=kge_id, error=str(exc))
        return HTMLResponse(_row_feedback(kge_id, f"error: {exc}"), status_code=500)
    finally:
        conn.close()


def _now_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_feedback(kge_id: int, msg: str) -> str:
    """HTMX swap target: replace the table row with a status message."""
    return (
        f'<tr id="kge-row-{kge_id}">'
        f'<td colspan="6" style="color:#888;font-style:italic">{msg}</td>'
        f"</tr>"
    )
