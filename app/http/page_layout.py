"""Shared HTML layout for admin UI pages (topbar + CSS)."""
from __future__ import annotations

_NAV = [
    ("jobs",   "/jobs",         "Задачи"),
    ("review", "/review",       "KGE&#8209;предсказания"),
    ("tier1",  "/review/tier1", "Кандидаты уровня&nbsp;1"),
    ("sparql", "/sparql",       "SPARQL&#8209;консоль"),
]

# Plain string — CSS braces are literals, not format specifiers.
_CSS = """
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
    .topbar { position: sticky; top: 0; z-index: 100; height: var(--topbar);
              background: var(--surface); border-bottom: 1px solid var(--border);
              display: flex; align-items: center; padding: 0 1.5rem;
              box-shadow: 0 1px 3px rgba(0,0,0,.06); }
    .brand { font-weight: 700; font-size: .92rem; color: var(--text); text-decoration: none;
             margin-right: 1.75rem; display: flex; align-items: center; gap: 7px; }
    .brand svg { opacity: .65; flex-shrink: 0; }
    .topbar-nav { display: flex; align-items: center; gap: 2px; }
    .nav-link { padding: 5px 11px; border-radius: 6px; font-size: .8rem; font-weight: 500;
                text-decoration: none; color: var(--muted); white-space: nowrap;
                transition: background .12s, color .12s; }
    .nav-link:hover  { background: var(--bg); color: var(--text); }
    .nav-link.active { background: #eff6ff; color: var(--blue); font-weight: 600; }
    /* layout */
    main { max-width: 1280px; margin: 0 auto; padding: 1.75rem 1.25rem 3rem; }
    .page-title { margin: 0 0 1.4rem; }
    .page-title h1 { font-size: 1.3rem; font-weight: 700; margin: 0 0 .1rem; }
    .page-title p  { color: var(--muted); margin: 0; font-size: .8rem; }
    /* table card */
    .table-card { border-radius: var(--radius); box-shadow: var(--shadow);
                  background: var(--surface); border: 1px solid var(--border); overflow: hidden; }
    table { width: 100%; border-collapse: collapse; font-size: .82rem; }
    th { background: #f8fafc; font-weight: 600; white-space: nowrap; padding: 9px 12px;
         text-align: left; color: var(--muted); font-size: .7rem;
         text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid var(--border); }
    td { padding: 8px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: #f8fafc; }
    code { font-size: .78rem; color: #5b21b6; background: #f5f3ff;
           padding: 1px 5px; border-radius: 4px; }
    /* action buttons */
    .btn-action { display: inline-flex; align-items: center; gap: 3px;
                  padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px;
                  font-size: .75rem; font-weight: 600; cursor: pointer; background: var(--surface);
                  transition: background .12s; line-height: 1.4; }
    .btn-action.approve { color: var(--green); border-color: #bbf7d0; }
    .btn-action.approve:hover { background: #f0fdf4; }
    .btn-action.reject  { color: var(--red);   border-color: #fecaca; }
    .btn-action.reject:hover  { background: #fff5f5; }
    .btn-action.defer   { color: var(--muted); }
    .btn-action.defer:hover   { background: var(--bg); }
    /* badges */
    .badge-count { display: inline-flex; align-items: center; justify-content: center;
                   min-width: 20px; height: 18px; padding: 0 6px;
                   background: var(--blue); color: #fff;
                   border-radius: 10px; font-size: .7rem; font-weight: 700; margin-left: 5px; }
    .badge-conflict { display: inline-block; padding: 1px 6px; border-radius: 4px;
                      font-size: .7rem; font-weight: 600; background: #fef3c7; color: #92400e; }
    /* misc */
    .page-note { color: var(--muted); font-size: .78rem; margin-top: .75rem; }
    .empty-state { color: var(--muted); text-align: center; padding: 2.5rem; font-size: .85rem; }
    .error-msg { color: var(--red); padding: 10px 14px; background: #fff5f5;
                 border: 1px solid #fecaca; border-radius: var(--radius); font-size: .82rem; }
    /* SPARQL */
    .query-card { background: var(--surface); border: 1px solid var(--border);
                  border-radius: var(--radius); padding: 1.25rem; box-shadow: var(--shadow); }
    textarea { width: 100%; height: 130px; font-family: monospace; font-size: .85rem;
               border: 1px solid var(--border); border-radius: var(--radius);
               padding: 10px 12px; background: var(--surface); color: var(--text);
               resize: vertical; outline: none; }
    textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(37,99,235,.1); }
    .run-btn { display: inline-flex; align-items: center; gap: 6px; margin-top: 10px;
               padding: 8px 18px; background: var(--blue); color: #fff; border: none;
               border-radius: 7px; cursor: pointer; font-size: .82rem; font-weight: 600;
               transition: background .13s; }
    .run-btn:hover { background: var(--blue-dk); }
    .htmx-request .run-btn { opacity: .6; pointer-events: none; }
    .result-note { color: var(--muted); font-size: .75rem; margin-top: .5rem; }
"""

_SVG_LOGO = (
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2">'
    '<path d="M12 2L2 7l10 5 10-5-10-5z"/>'
    '<path d="M2 17l10 5 10-5"/>'
    '<path d="M2 12l10 5 10-5"/>'
    '</svg>'
)


def page_open(title: str, active: str) -> str:
    """Return opening HTML up to and including <main>."""
    nav = "".join(
        f'<a class="nav-link{" active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in _NAV
    )
    return (
        f'<!DOCTYPE html>\n<html lang="ru">\n<head>\n'
        f'  <meta charset="utf-8">\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'  <title>{title} — Knowledge Base</title>\n'
        f'  <script src="https://unpkg.com/htmx.org@1.9.12" defer></script>\n'
        f'  <style>{_CSS}  </style>\n'
        f'</head>\n<body>\n'
        f'  <header class="topbar">\n'
        f'    <a class="brand" href="/">{_SVG_LOGO} Knowledge Base</a>\n'
        f'    <nav class="topbar-nav">{nav}</nav>\n'
        f'  </header>\n'
        f'  <main>\n'
    )


def page_close() -> str:
    return "  </main>\n</body>\n</html>"
