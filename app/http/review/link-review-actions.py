"""
POST /review/links/{id}/approve  → insert [[wikilink]] at end of source .md file
POST /review/links/{id}/reject   → mark rejected
POST /review/links/{id}/defer    → re-show after 7 days
POST /review/links/discover      → run discovery synchronously (background-friendly)
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import REPO_ROOT
from app.logging import get_logger

router = APIRouter(tags=["review"])
log = get_logger(__name__)


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_feedback(row_id: int, msg: str) -> str:
    return (
        f'<tr id="slc-row-{row_id}">'
        f'<td colspan="4" style="color:#888;font-style:italic">{msg}</td>'
        f"</tr>"
    )


def _get_vault_map(container) -> dict:
    """Build {vault_id: VaultConfig} from app config."""
    return {v.id: v for v in container.cfg.sources.vaults}


def _insert_wikilink(note_path: Path, link_title: str) -> None:
    """Append [[link_title]] to the end of the note file."""
    content = note_path.read_text(encoding="utf-8")
    if not content.endswith("\n"):
        content += "\n"
    content += f"\n[[{link_title}]]\n"
    note_path.write_text(content, encoding="utf-8")


@router.post("/review/links/{item_id}/approve", response_class=HTMLResponse)
async def approve_link(request: Request, item_id: int) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        row = conn.execute(
            "SELECT vault_id, source_note_id, target_note_id FROM semantic_link_candidates WHERE id=?",
            (item_id,),
        ).fetchone()
        if not row:
            return HTMLResponse(_row_feedback(item_id, "не найдено"), status_code=404)

        vault_id, source_id, target_id = row[0], row[1], row[2]
        vault_map = _get_vault_map(container)
        vault = vault_map.get(vault_id)
        if not vault:
            return HTMLResponse(_row_feedback(item_id, f"vault '{vault_id}' не найден"), status_code=400)

        # Resolve source note path
        source_rel = source_id.split(":", 1)[1] if ":" in source_id else source_id
        source_path = vault.path / source_rel
        if not source_path.exists():
            return HTMLResponse(_row_feedback(item_id, f"файл не найден: {source_rel}"), status_code=400)

        # Derive link title from target filename stem
        target_rel = target_id.split(":", 1)[1] if ":" in target_id else target_id
        link_title = Path(target_rel).stem

        _insert_wikilink(source_path, link_title)

        conn.execute(
            "UPDATE semantic_link_candidates SET status='approved', reviewed_at=? WHERE id=?",
            (_now(), item_id),
        )
        conn.commit()
        log.info("link_approved", item_id=item_id, source=source_id, target=target_id)
        return HTMLResponse(_row_feedback(item_id, f"&#10003; [[{link_title}]] добавлена в {Path(source_rel).name}"))
    except Exception as exc:
        log.error("link_approve_failed", item_id=item_id, error=str(exc))
        return HTMLResponse(_row_feedback(item_id, f"ошибка: {exc}"), status_code=500)
    finally:
        conn.close()


@router.post("/review/links/{item_id}/reject", response_class=HTMLResponse)
async def reject_link(request: Request, item_id: int) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        conn.execute(
            "UPDATE semantic_link_candidates SET status='rejected', reviewed_at=? WHERE id=?",
            (_now(), item_id),
        )
        conn.commit()
        log.info("link_rejected", item_id=item_id)
        return HTMLResponse(_row_feedback(item_id, "&#10007; отклонено"))
    except Exception as exc:
        log.error("link_reject_failed", item_id=item_id, error=str(exc))
        return HTMLResponse(_row_feedback(item_id, f"ошибка: {exc}"), status_code=500)
    finally:
        conn.close()


@router.post("/review/links/{item_id}/defer", response_class=HTMLResponse)
async def defer_link(request: Request, item_id: int) -> HTMLResponse:
    container = request.app.state.container
    conn = container.sqlite.connect()
    try:
        defer_until = (datetime.now(tz=timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE semantic_link_candidates SET status='deferred', defer_until=? WHERE id=?",
            (defer_until, item_id),
        )
        conn.commit()
        log.info("link_deferred", item_id=item_id, until=defer_until)
        return HTMLResponse(_row_feedback(item_id, "&#8987; отложено на 7 дней"))
    except Exception as exc:
        log.error("link_defer_failed", item_id=item_id, error=str(exc))
        return HTMLResponse(_row_feedback(item_id, f"ошибка: {exc}"), status_code=500)
    finally:
        conn.close()


@router.post("/review/links/discover")
async def trigger_discover(request: Request) -> JSONResponse:
    """Run semantic link discovery synchronously for all vaults."""
    container = request.app.state.container
    try:
        discoverer = _load_discoverer()
        conn = container.sqlite.connect()
        try:
            results = discoverer.discover_all_vaults(
                vaults=container.cfg.sources.vaults,
                qdrant=container.qdrant,
                conn=conn,
            )
        finally:
            conn.close()
        total = sum(results.values())
        detail = ", ".join(f"{v}: {n}" for v, n in results.items()) if results else "нет хранилищ"
        return JSONResponse({"ok": True, "inserted": total, "message": f"Найдено {total} новых кандидатов ({detail})"})
    except Exception as exc:
        log.error("link_discover_failed", error=str(exc))
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


def _load_discoverer():
    name = "_link_discoverer_mod"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "app" / "services" / "semantic-links" / "link-discoverer.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
