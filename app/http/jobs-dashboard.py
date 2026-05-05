"""
/jobs — панель очереди задач (статистика + последние задачи, htmx auto-refresh).

GET  /jobs                  → HTML-страница
GET  /jobs/stats            → JSON {pending, running, done, failed, total}
GET  /jobs/fragment/stats   → htmx: карточки статистики
GET  /jobs/fragment/recent  → htmx: таблица задач (?page=N)
POST /jobs/scan             → поставить в очередь все vault'ы + книги (idempotent)
POST /jobs/retry-failed     → сбросить задачи с ошибкой обратно в очередь
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.logging import get_logger

router = APIRouter(tags=["jobs"])
log = get_logger(__name__)

PAGE_SIZE = 50

_STATUS_RU = {
    "pending": "в очереди",
    "running": "выполняется",
    "done":    "готово",
    "failed":  "ошибка",
}

_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Задачи — Knowledge Base</title>
  <script src="https://unpkg.com/htmx.org@1.9.12" defer></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    :root {
      --bg: #f1f5f9; --surface: #fff; --border: #e2e8f0;
      --text: #0f172a; --muted: #64748b;
      --blue: #2563eb; --blue-dk: #1d4ed8;
      --green: #16a34a; --yellow: #d97706; --red: #dc2626;
      --radius: 10px;
      --shadow: 0 1px 4px rgba(0,0,0,.07), 0 4px 16px rgba(0,0,0,.06);
      --topbar: 52px;
    }
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif;
           background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.5; }

    /* topbar */
    .topbar {
      position: sticky; top: 0; z-index: 100; height: var(--topbar);
      background: var(--surface); border-bottom: 1px solid var(--border);
      display: flex; align-items: center; padding: 0 1.5rem; gap: 0;
      box-shadow: 0 1px 3px rgba(0,0,0,.06);
    }
    .brand {
      font-weight: 700; font-size: .92rem; color: var(--text);
      text-decoration: none; margin-right: 1.75rem; display: flex; align-items: center; gap: 7px;
    }
    .brand svg { opacity: .65; flex-shrink: 0; }
    .topbar-nav { display: flex; align-items: center; gap: 2px; }
    .nav-link {
      padding: 5px 11px; border-radius: 6px; font-size: .8rem; font-weight: 500;
      text-decoration: none; color: var(--muted); white-space: nowrap;
      transition: background .12s, color .12s;
    }
    .nav-link:hover  { background: var(--bg); color: var(--text); }
    .nav-link.active { background: #eff6ff; color: var(--blue); font-weight: 600; }

    /* main */
    main { max-width: 1280px; margin: 0 auto; padding: 1.75rem 1.25rem 3rem; }

    /* page title */
    .page-title { margin: 0 0 1.4rem; }
    .page-title h1 { font-size: 1.3rem; font-weight: 700; margin: 0 0 .1rem; }
    .page-title p  { color: var(--muted); margin: 0; font-size: .8rem; }

    /* stat cards */
    .cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 1.25rem; }
    .card  { flex: 1; min-width: 115px; padding: 15px 18px; border-radius: var(--radius);
             background: var(--surface); border: 1px solid var(--border); box-shadow: var(--shadow); }
    .card .label { font-size: .68rem; font-weight: 600; color: var(--muted);
                   text-transform: uppercase; letter-spacing: .06em; }
    .card .value { font-size: 1.9rem; font-weight: 800; margin-top: 3px; line-height: 1; }
    .card.pending .value { color: var(--yellow); }
    .card.running .value { color: var(--blue); }
    .card.done    .value { color: var(--green); }
    .card.failed  .value { color: var(--red); }
    .card.total   .value { color: #334155; }

    /* actions */
    .actions { display: flex; align-items: center; gap: 8px; margin-bottom: 1.4rem; flex-wrap: wrap; }
    .btn { display: inline-flex; align-items: center; gap: 5px; padding: 7px 15px;
           border: none; border-radius: 7px; cursor: pointer;
           font-size: .8rem; font-weight: 600; transition: background .13s; }
    .btn-primary { background: var(--blue); color: #fff; }
    .btn-primary:hover { background: var(--blue-dk); }
    .btn-warning { background: var(--surface); color: var(--red); border: 1px solid #fecaca; }
    .btn-warning:hover { background: #fff5f5; }
    .btn:disabled { opacity: .5; cursor: default; }
    .msg { font-size: .8rem; color: var(--green); }
    .msg.err { color: var(--red); }

    /* section head */
    .section-head { display: flex; align-items: center; justify-content: space-between; margin: 0 0 .55rem; }
    .section-head h2 { font-size: .88rem; font-weight: 700; margin: 0; }
    .hint { color: var(--muted); font-size: .73rem; }

    /* table card */
    .table-card { border-radius: var(--radius); box-shadow: var(--shadow);
                  background: var(--surface); border: 1px solid var(--border); overflow: hidden; }
    .table-wrap  { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: .79rem; }
    thead { }
    th { background: #f8fafc; font-weight: 600; white-space: nowrap;
         padding: 9px 12px; text-align: left; color: var(--muted);
         font-size: .69rem; text-transform: uppercase; letter-spacing: .05em;
         border-bottom: 1px solid var(--border); }
    td { padding: 7px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: #f8fafc; }
    td.path { max-width: 290px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              color: var(--muted); font-family: monospace; font-size: .74rem; }
    td.ts   { white-space: nowrap; color: var(--muted); font-size: .74rem; }
    td.num  { text-align: center; color: var(--muted); }
    td.dur  { white-space: nowrap; font-family: monospace; font-size: .74rem; color: var(--muted); }

    /* badges */
    .badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 9px;
             border-radius: 20px; font-size: .71rem; font-weight: 600; white-space: nowrap; }
    .badge::before { content: ''; width: 6px; height: 6px; border-radius: 50%;
                     background: currentColor; opacity: .6; }
    .badge.pending { background: #fef9c3; color: #854d0e; }
    .badge.running { background: #dbeafe; color: #1e40af; }
    .badge.running.stale { background: #f1f5f9; color: var(--muted); }
    .badge.done    { background: #dcfce7; color: #166534; }
    .badge.failed  { background: #fee2e2; color: #991b1b; }

    /* error cell */
    .error-cell      { color: var(--red); font-size: .74rem; max-width: 240px;
                        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: help; }
    .error-cell.past { color: #94a3b8; }

    /* pagination */
    .pagination { display: flex; align-items: center; justify-content: space-between;
                  padding: 10px 14px; border-top: 1px solid var(--border);
                  background: #fafbfc; font-size: .78rem; gap: 8px; flex-wrap: wrap; }
    .pag-info { color: var(--muted); }
    .pag-btns { display: flex; gap: 3px; }
    .pg {
      padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px;
      background: var(--surface); cursor: pointer; font-size: .76rem; color: var(--text);
      transition: background .1s; line-height: 1.4;
    }
    .pg:hover:not([disabled]) { background: var(--bg); }
    .pg.cur { background: var(--blue); color: #fff; border-color: var(--blue); }
    .pg[disabled] { opacity: .38; cursor: default; }

    /* loading */
    .loading { color: var(--muted); font-size: .82rem; padding: 2.5rem; text-align: center; }
  </style>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2L2 7l10 5 10-5-10-5z"/>
        <path d="M2 17l10 5 10-5"/>
        <path d="M2 12l10 5 10-5"/>
      </svg>
      Knowledge Base
    </a>
    <nav class="topbar-nav">
      <a class="nav-link active" href="/jobs">Задачи</a>
      <a class="nav-link" href="/review">KGE-предсказания</a>
      <a class="nav-link" href="/review/tier1">Кандидаты уровня&nbsp;1</a>
      <a class="nav-link" href="/sparql">SPARQL-консоль</a>
    </nav>
  </header>

  <main>
    <div class="page-title">
      <h1>Панель задач</h1>
      <p>Очередь фоновых задач &mdash; обновляется каждые 5&thinsp;с</p>
    </div>

    <div hx-get="/jobs/fragment/stats" hx-trigger="load, every 5s" hx-swap="innerHTML">
      <div class="cards">
        <div class="card pending"><div class="label">В очереди</div> <div class="value">…</div></div>
        <div class="card running"><div class="label">Выполняется</div><div class="value">…</div></div>
        <div class="card done">   <div class="label">Завершено</div>  <div class="value">…</div></div>
        <div class="card failed"> <div class="label">Ошибки</div>     <div class="value">…</div></div>
        <div class="card total">  <div class="label">Всего</div>      <div class="value">…</div></div>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-primary"
              hx-post="/jobs/scan"
              hx-target="#action-msg"
              hx-swap="innerHTML">
        &#9654; Сканировать источники
      </button>
      <button class="btn btn-warning"
              hx-post="/jobs/retry-failed"
              hx-target="#action-msg"
              hx-swap="innerHTML"
              hx-confirm="Сбросить все задачи с ошибкой обратно в очередь?">
        &#8635; Повторить ошибки
      </button>
      <span id="action-msg"></span>
    </div>

    <div class="section-head">
      <h2>Последние задачи</h2>
      <span class="hint">Наведите на ячейку ошибки для полного текста</span>
    </div>
    <div class="table-card">
      <div id="jobs-table"
           class="table-wrap"
           hx-get="/jobs/fragment/recent"
           hx-trigger="load, every 5s"
           hx-vals="js:{page: window._jobsPage || 1}"
           hx-swap="innerHTML">
        <p class="loading">Загрузка&hellip;</p>
      </div>
    </div>
  </main>

  <script>
    window._jobsPage = 1;
    function gotoPage(p) {
      window._jobsPage = p;
      htmx.ajax('GET', '/jobs/fragment/recent?page=' + p, {target: '#jobs-table', swap: 'innerHTML'});
    }
  </script>
</body>
</html>"""


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page() -> HTMLResponse:
    return HTMLResponse(_PAGE)


@router.get("/jobs/stats")
async def jobs_stats(request: Request) -> JSONResponse:
    loop = asyncio.get_event_loop()
    s = await loop.run_in_executor(None, _query_stats, request)
    return JSONResponse(s)


@router.get("/jobs/fragment/stats", response_class=HTMLResponse)
async def jobs_stats_fragment(request: Request) -> HTMLResponse:
    loop = asyncio.get_event_loop()
    s = await loop.run_in_executor(None, _query_stats, request)
    html = (
        '<div class="cards">'
        + _card("pending", "В очереди",   s["pending"])
        + _card("running", "Выполняется", s["running"])
        + _card("done",    "Завершено",   s["done"])
        + _card("failed",  "Ошибки",      s["failed"])
        + _card("total",   "Всего",       s["total"])
        + "</div>"
    )
    return HTMLResponse(html)


@router.get("/jobs/fragment/recent", response_class=HTMLResponse)
async def jobs_recent_fragment(
    request: Request,
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    loop = asyncio.get_event_loop()
    rows, total = await loop.run_in_executor(None, _query_recent, request, page, PAGE_SIZE)
    return HTMLResponse(_render_recent_table(rows, page, PAGE_SIZE, total))


@router.post("/jobs/scan", response_class=HTMLResponse)
async def jobs_scan(request: Request) -> HTMLResponse:
    """Поставить в очередь все vault'ы и PDF-книги (idempotent)."""
    container = request.app.state.container
    cfg = container.cfg
    dispatcher = container.dispatcher

    from pathlib import Path as _P
    from app.jobs import types as T

    notes = books = 0
    for vault in cfg.sources.vaults:
        vp = _P(vault.path)
        if not vp.exists():
            continue
        for f in vp.rglob("*.md"):
            await dispatcher.enqueue(
                T.INDEX_NOTE,
                {"path": str(f), "source_type": "vault", "source_id": vault.id},
                priority=5,
                idempotency_key=f"index_note:{f.resolve()}",
            )
            notes += 1

    for bp in cfg.sources.book_paths:
        bp = _P(bp)
        if not bp.exists():
            continue
        for f in bp.rglob("*.pdf"):
            await dispatcher.enqueue(
                T.PARSE_BOOK,
                {"path": str(f), "priority": 5},
                priority=5,
                idempotency_key=f"parse_book:{f.resolve()}",
            )
            books += 1

    log.info("jobs_scan_triggered", notes=notes, books=books)
    return HTMLResponse(
        f'<span class="msg">Добавлено в очередь: {notes} заметок + {books} книг</span>'
    )


@router.post("/jobs/retry-failed", response_class=HTMLResponse)
async def jobs_retry_failed(request: Request) -> HTMLResponse:
    """Сбросить все задачи с ошибкой обратно в очередь."""
    sqlite = request.app.state.container.sqlite
    conn = sqlite.connect()
    try:
        cur = conn.execute(
            """UPDATE jobs
               SET status='pending', attempts=0, error=NULL,
                   scheduled_after=NULL, started_at=NULL, finished_at=NULL
               WHERE status='failed'"""
        )
        conn.commit()
        count = cur.rowcount
    finally:
        conn.close()
    log.info("jobs_retry_failed", count=count)
    return HTMLResponse(f'<span class="msg">Сброшено {count} задач</span>')


# ── helpers ───────────────────────────────────────────────────────────────────

def _query_stats(request: Request) -> dict:
    conn = request.app.state.container.sqlite.connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    counts: dict[str, int] = {r["status"]: r["n"] for r in rows}
    p = counts.get("pending", 0)
    r = counts.get("running", 0)
    d = counts.get("done",    0)
    f = counts.get("failed",  0)
    return {"pending": p, "running": r, "done": d, "failed": f, "total": p + r + d + f}


def _query_recent(request: Request, page: int, page_size: int) -> tuple[list[dict], int]:
    conn = request.app.state.container.sqlite.connect()
    offset = (page - 1) * page_size
    try:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        rows = conn.execute(
            """SELECT id, type, status, payload_json,
                      created_at, started_at, finished_at, attempts, error
               FROM jobs ORDER BY id DESC LIMIT ? OFFSET ?""",
            (page_size, offset),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows], total


def _extract_path(payload_json: str) -> str:
    try:
        p = json.loads(payload_json or "{}")
        return p.get("path") or p.get("url") or p.get("new_path") or ""
    except Exception:
        return ""


def _to_local(utc_str: str) -> str:
    if not utc_str:
        return ""
    try:
        import time
        from datetime import datetime, timezone, timedelta
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        offset = timedelta(seconds=-time.timezone)
        if time.daylight and time.localtime().tm_isdst:
            offset = timedelta(seconds=-time.altzone)
        return (dt + offset).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return utc_str[:16]


def _duration(started: str | None, finished: str | None) -> str:
    if not started or not finished:
        return ""
    try:
        from datetime import datetime
        s = datetime.strptime(started,  "%Y-%m-%d %H:%M:%S")
        f = datetime.strptime(finished, "%Y-%m-%d %H:%M:%S")
        return f"{(f - s).total_seconds():.1f}s"
    except Exception:
        return ""


def _card(cls: str, label: str, value: int) -> str:
    return (
        f'<div class="card {cls}">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f"</div>"
    )


def _render_recent_table(rows: list[dict], page: int, page_size: int, total: int) -> str:
    if not rows:
        return '<p class="loading">Задачи не найдены.</p>'

    header = (
        "<table><thead><tr>"
        "<th>#</th><th>Тип</th><th>Файл / URL</th>"
        "<th>Создана</th><th>Статус</th><th>Попытки</th><th>Время</th><th>Ошибка</th>"
        "</tr></thead><tbody>"
    )
    body = ""
    for r in rows:
        path   = _esc(_extract_path(r.get("payload_json", "")))
        status = r.get("status", "")
        err    = r.get("error") or ""

        stale       = status == "running" and err
        badge_cls   = f"badge {status}" + (" stale" if stale else "")
        badge_label = "завис" if stale else _STATUS_RU.get(status, status)
        badge       = f'<span class="{badge_cls}">{badge_label}</span>'

        dur = _duration(r.get("started_at"), r.get("finished_at"))

        if err:
            if status == "done":
                err_cell = f'<span class="error-cell past" title="прошлая попытка: {_esc(err)}">ранее: {_esc(err[:50])}</span>'
            else:
                err_cell = f'<span class="error-cell" title="{_esc(err)}">{_esc(err[:60])}</span>'
        else:
            err_cell = ""

        created = _to_local(r.get("created_at") or "")
        body += (
            f"<tr>"
            f'<td class="num">{r["id"]}</td>'
            f"<td>{_esc(r.get('type', ''))}</td>"
            f'<td class="path" title="{path}">{path}</td>'
            f'<td class="ts">{created}</td>'
            f"<td>{badge}</td>"
            f'<td class="num">{r.get("attempts", 0)}</td>'
            f'<td class="dur">{dur}</td>'
            f"<td>{err_cell}</td>"
            f"</tr>"
        )

    table = header + body + "</tbody></table>"
    pagination = _render_pagination(page, page_size, total)
    return table + pagination


def _render_pagination(page: int, page_size: int, total: int) -> str:
    if total <= page_size:
        return ""
    total_pages = (total + page_size - 1) // page_size
    prev_disabled = 'disabled' if page <= 1 else ''
    next_disabled = 'disabled' if page >= total_pages else ''
    start = (page - 1) * page_size + 1
    end   = min(page * page_size, total)

    btns = f'<button class="pg" {prev_disabled} onclick="gotoPage({page - 1})">&larr;</button>'

    # show up to 7 page buttons around current
    lo = max(1, page - 3)
    hi = min(total_pages, page + 3)
    if lo > 1:
        btns += f'<button class="pg" onclick="gotoPage(1)">1</button>'
        if lo > 2:
            btns += '<span style="padding:0 4px;color:#94a3b8">…</span>'
    for p in range(lo, hi + 1):
        cur = ' cur' if p == page else ''
        btns += f'<button class="pg{cur}" onclick="gotoPage({p})">{p}</button>'
    if hi < total_pages:
        if hi < total_pages - 1:
            btns += '<span style="padding:0 4px;color:#94a3b8">…</span>'
        btns += f'<button class="pg" onclick="gotoPage({total_pages})">{total_pages}</button>'

    btns += f'<button class="pg" {next_disabled} onclick="gotoPage({page + 1})">&rarr;</button>'

    return (
        f'<div class="pagination">'
        f'<span class="pag-info">{start}–{end} из {total}</span>'
        f'<div class="pag-btns">{btns}</div>'
        f'</div>'
    )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
