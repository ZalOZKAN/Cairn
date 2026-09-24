"""cairn memory dashboard: single-file HTML view of project memory.

No server. No build step. One Python module that collects decisions from a
project's SQLite memory (plus the cross-project ~/.cairn/global.db, when
enabled) and renders a self-contained HTML file: a force-directed decision
graph (nodes = decisions, edges = supersedes chains), a card-grid list view,
modal drill-down, and a 30-day activity chart. When global memory has
entries, a scope tab (This Project / Global) switches which one set of
decisions the graph and card views show; the two scopes are never rendered
on the same canvas at once. A persistent toolbar (search, status filter,
view toggle) stays visible in both the graph and card views. Tailwind /
Chart.js / D3 are pulled from CDNs at view time; D3's force layout
(`d3-force`) drives the graph.

Generated file lives at `.cairn/persistence/dashboard.html`.
"""
from __future__ import annotations

import json
import sqlite3
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import typer

from cairn.memory.memory import sqlite_connect


FILTERS = {"all", "active", "invalidated", "recent"}


def _resolve_db_paths(config: dict) -> tuple[Path, Path | None]:
    mem_cfg = config.get("memory", {}) or {}
    db_path = Path(mem_cfg.get("db_path", "~/.cairn/memory.db")).expanduser()
    global_db_path = None
    if mem_cfg.get("global_enabled", True):
        global_db_path = Path(mem_cfg.get("global_db_path", "~/.cairn/global.db")).expanduser()
    return db_path, global_db_path


def collect_dashboard_data(
    db_path: Path,
    filt: str = "all",
    global_db_path: Path | None = None,
    other_projects: list[dict] | None = None,
) -> dict[str, Any]:
    """Read decisions from SQLite (project + optional global) and produce
    the JSON data island. Each decision is tagged `scope: "project"|"global"`;
    global-origin ids are prefixed `"g<id>"`, matching the convention used
    everywhere else in Cairn (MCP tools, CLI) to disambiguate the two DBs'
    independent id sequences. `other_projects` (from `cairn.config.registry`)
    lists sibling Cairn projects on this machine for the dashboard's project
    switcher; it never affects which decisions are loaded."""
    if filt not in FILTERS:
        filt = "all"

    decisions: list[dict] = []
    activity: list[dict] = []
    project_active = project_invalidated = 0
    global_active = global_invalidated = 0

    if db_path.exists():
        with sqlite_connect(db_path) as conn:
            decisions.extend(_load_decisions(conn, filt, scope="project"))
            activity = _load_activity(conn)
            project_active = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NULL"
            ).fetchone()[0]
            project_invalidated = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NOT NULL"
            ).fetchone()[0]

    if global_db_path is not None and global_db_path.exists():
        with sqlite_connect(global_db_path) as conn:
            decisions.extend(_load_decisions(conn, filt, scope="global"))
            global_active = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NULL"
            ).fetchone()[0]
            global_invalidated = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NOT NULL"
            ).fetchone()[0]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": filt,
        "active_count": project_active + global_active,
        "invalidated_count": project_invalidated + global_invalidated,
        "project_active_count": project_active,
        "project_invalidated_count": project_invalidated,
        "global_active_count": global_active,
        "global_invalidated_count": global_invalidated,
        "has_global": global_db_path is not None,
        "activity": activity,
        "decisions": decisions,
        "other_projects": other_projects or [],
    }


def _empty_data(filt: str) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": filt,
        "active_count": 0,
        "invalidated_count": 0,
        "project_active_count": 0,
        "project_invalidated_count": 0,
        "global_active_count": 0,
        "global_invalidated_count": 0,
        "has_global": False,
        "activity": [],
        "decisions": [],
    }


def _load_decisions(conn: sqlite3.Connection, filt: str, scope: str = "project") -> list[dict]:
    where = "WHERE kind='decision'"
    if filt == "active":
        where += " AND valid_until IS NULL"
    elif filt == "invalidated":
        where += " AND valid_until IS NOT NULL"
    elif filt == "recent":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        where += f" AND timestamp > '{cutoff}'"

    rows = conn.execute(
        f"SELECT id, timestamp, prompt, reasoning, alternatives_json, "
        f"superseded_by, chose_because, valid_until, importance "
        f"FROM memories {where} ORDER BY timestamp DESC LIMIT 200"
    ).fetchall()

    is_global = scope == "global"
    out = []
    for r in rows:
        raw_id = r[0]
        evidence = [
            {"source": s, "claim": c}
            for s, c in conn.execute(
                "SELECT source, claim FROM memory_evidence WHERE memory_id=?", (raw_id,)
            ).fetchall()
        ]
        alts = json.loads(r[4]) if r[4] else []
        version_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE superseded_by=? OR id=?", (raw_id, raw_id)
        ).fetchone()[0]
        score = _audit_score(r[3], alts, evidence, r[5])
        sup_by = r[5]
        out.append({
            "id": f"g{raw_id}" if is_global else raw_id,
            "timestamp": r[1],
            "decision": r[2],
            "reasoning": r[3] or "",
            "alternatives": alts,
            "evidence": evidence,
            "superseded_by": f"g{sup_by}" if (is_global and sup_by is not None) else sup_by,
            "chose_because": r[6] or "",
            "active": r[7] is None,
            "importance": r[8] or 1,
            "version_count": version_count,
            "audit_score": score,
            "scope": scope,
        })
    return out


def _load_activity(conn: sqlite3.Connection) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = conn.execute(
        "SELECT substr(timestamp,1,10) AS day, COUNT(*) "
        "FROM memories WHERE timestamp > ? GROUP BY day ORDER BY day",
        (cutoff,),
    ).fetchall()
    return [{"day": d, "count": c} for d, c in rows]


def _audit_score(reasoning: str | None, alts: list, evidence: list, superseded_by: int | None) -> int:
    return sum([
        bool(reasoning), bool(alts), bool(evidence), superseded_by is not None,
    ])


def render_dashboard_html(data: dict[str, Any]) -> str:
    """Render the single-file HTML. Data is embedded as a JSON island."""
    data_json = json.dumps(data, ensure_ascii=False)
    return _HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


def _other_projects(config: dict, project_dir: Path) -> list[dict]:
    """Register `project_dir` in ~/.cairn/projects.json and return every
    other registered Cairn project, each with a `dashboard_uri` (a file://
    link to that project's own dashboard.html, or None if it hasn't
    generated one yet). Never merges another project's memory data, only
    lets the dashboard link out to it."""
    from cairn.config.registry import list_projects, register_project

    mem_cfg = config.get("memory", {}) or {}
    registry_path = Path(mem_cfg.get("registry_path", "~/.cairn/projects.json")).expanduser()
    register_project(project_dir, registry_path)

    current = project_dir.resolve()
    others = []
    for p in list_projects(registry_path):
        other_path = Path(p["path"])
        if other_path == current:
            continue
        dashboard_file = other_path / ".cairn" / "persistence" / "dashboard.html"
        others.append({
            "name": p["name"],
            "path": p["path"],
            "last_seen": p["last_seen"],
            "dashboard_uri": dashboard_file.as_uri() if dashboard_file.exists() else None,
        })
    return others


def write_dashboard(project_dir: Path, filt: str = "all") -> Path:
    """Generate the dashboard HTML at .cairn/persistence/dashboard.html."""
    from cairn.config.loader import load_config
    config = load_config()
    db_path, global_db_path = _resolve_db_paths(config)
    other_projects = _other_projects(config, project_dir)
    data = collect_dashboard_data(
        db_path, filt=filt, global_db_path=global_db_path, other_projects=other_projects
    )
    html = render_dashboard_html(data)
    out_dir = project_dir / ".cairn" / "persistence"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


def dashboard_command(
    open_browser: bool = typer.Option(False, "--open", help="Open in default browser after writing"),
    filt: str = typer.Option("all", "--filter", help="all | active | invalidated | recent"),
    fmt: str = typer.Option("html", "--format", help="html | json"),
) -> None:
    """Generate the project dashboard as a single self-contained HTML file."""
    from cairn.config.loader import load_config
    config = load_config()
    db_path, global_db_path = _resolve_db_paths(config)
    project_dir = Path.cwd()
    other_projects = _other_projects(config, project_dir)
    data = collect_dashboard_data(
        db_path, filt=filt, global_db_path=global_db_path, other_projects=other_projects
    )
    if fmt == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    out = write_dashboard(project_dir, filt=filt)
    typer.echo(f"Dashboard written: {out}")
    if open_browser:
        webbrowser.open(out.as_uri())


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cairn Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
  :root {
    --bg-app: #f9fafb;
    --text-main: #111827;
    --text-muted: #4b5563;
    --border-color: #e5e7eb;
    --card-bg: #ffffff;
    --header-bg: rgba(243, 244, 246, 0.8);
    --modal-bg: #ffffff;
    --modal-border: #d1d5db;
    --graph-bg: #f8fafc;
  }

  .dark {
    --bg-app: #0b1020;
    --text-main: #f3f4f6;
    --text-muted: #9ca3af;
    --border-color: #1f2937;
    --card-bg: #111827;
    --header-bg: rgba(17, 24, 39, 0.8);
    --modal-bg: #111827;
    --modal-border: #374151;
    --graph-bg: #0d1220;
  }

  body { background: var(--bg-app); color: var(--text-main); font-family: ui-sans-serif, system-ui, sans-serif; transition: background-color 0.2s, color 0.2s; }
  .card { background: var(--card-bg); border: 1px solid var(--border-color); transition: border-color 0.2s, background-color 0.2s; }
  .card:hover { border-color: #3b82f6; cursor: pointer; }
  .badge-4 { background: #059669; color: white; }
  .badge-3 { background: #2563eb; color: white; }
  .badge-2 { background: #ea580c; color: white; }
  .badge-1 { background: #dc2626; color: white; }
  .badge-0 { background: #4b5563; color: white; }
  .modal-backdrop { background: rgba(0,0,0,0.5); backdrop-filter: blur(4px); }
  .modal-content { background: var(--modal-bg); border: 1px solid var(--modal-border); }

  /* Override Tailwind helper classes using CSS variables */
  .text-gray-400 { color: var(--text-muted) !important; }
  .text-gray-500 { color: var(--text-muted) !important; }
  .text-gray-600 { color: var(--text-muted) !important; }
  .bg-gray-900 { background-color: var(--modal-bg) !important; }
  .border-gray-700 { border-color: var(--modal-border) !important; }
  .border-gray-800 { border-color: var(--border-color) !important; }

  .view-btn, .filter-btn, .scope-btn {
    padding: 4px 12px; border-radius: 8px; font-size: 12px; font-weight: 500;
    border: 1px solid var(--border-color); background: var(--card-bg);
    color: var(--text-muted); cursor: pointer; transition: all 0.15s;
  }
  .view-btn.active, .filter-btn.active, .scope-btn.active { background: #3b82f6; color: white; border-color: #3b82f6; }

  #searchInput {
    background: var(--card-bg); border: 1px solid var(--border-color); color: var(--text-main);
    border-radius: 8px; padding: 6px 12px 6px 30px; font-size: 13px; width: 220px;
  }
  #searchInput:focus { outline: none; border-color: #3b82f6; }
  .search-wrap { position: relative; }
  .search-wrap svg { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); pointer-events: none; opacity: 0.5; }

  #toolbar {
    position: sticky; top: 0; z-index: 20; backdrop-filter: blur(6px);
    background: var(--header-bg); border-bottom: 1px solid var(--border-color);
  }

  .toolbar-cluster { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; }
  .toolbar-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-muted); font-weight: 700; padding-left: 2px; }
  .toolbar-group { display: flex; align-items: center; gap: 4px; padding: 3px; border-radius: 10px; background: var(--bg-app); border: 1px solid var(--border-color); }

  #scopeTabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border-color); }
  .scope-tab {
    padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    border: none; background: transparent; color: var(--text-muted);
    border-bottom: 2px solid transparent; margin-bottom: -1px; transition: all 0.15s;
  }
  .scope-tab.active { color: #3b82f6; border-bottom-color: #3b82f6; }
  .scope-tab .n { font-weight: 400; opacity: 0.7; }

  #resultCount { font-size: 11px; color: var(--text-muted); }

  #projectSwitcherBtn {
    padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 500;
    border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-main);
    cursor: pointer; display: flex; align-items: center; gap: 6px;
  }
  #projectSwitcherBtn:hover { border-color: #3b82f6; }
  #projectSwitcherMenu {
    position: absolute; top: calc(100% + 6px); right: 0; z-index: 30; min-width: 240px;
    background: var(--modal-bg); border: 1px solid var(--modal-border); border-radius: 10px;
    padding: 6px; box-shadow: 0 8px 24px rgba(0,0,0,0.2); max-height: 280px; overflow-y: auto;
  }
  #projectSwitcherMenu .psm-label { font-size: 10px; text-transform: uppercase; color: var(--text-muted); padding: 4px 8px; }
  #projectSwitcherMenu a {
    display: block; padding: 6px 8px; border-radius: 6px; font-size: 13px; color: var(--text-main);
    text-decoration: none;
  }
  #projectSwitcherMenu a:hover { background: var(--bg-app); }
  #projectSwitcherMenu a.disabled { color: var(--text-muted); cursor: default; pointer-events: none; }
  #projectSwitcherMenu .psm-path { display: block; font-size: 10px; color: var(--text-muted); }

  #graphContainer { background: var(--graph-bg); cursor: grab; }
  #graphContainer.panning { cursor: grabbing; }
  .graph-node { transition: opacity 0.15s; }
  .graph-node text { pointer-events: none; user-select: none; transition: opacity 0.1s; }
  .graph-node.pinned circle { stroke-dasharray: 3,2; }
  .graph-legend span { display: inline-flex; align-items: center; gap: 4px; margin-right: 12px; }
  .graph-legend .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }

  #graphControls { position: absolute; bottom: 10px; right: 10px; display: flex; flex-direction: column; gap: 4px; }
  #graphControls button {
    width: 28px; height: 28px; border-radius: 6px; border: 1px solid var(--border-color);
    background: var(--card-bg); color: var(--text-main); cursor: pointer; font-size: 15px;
    line-height: 1; display: flex; align-items: center; justify-content: center;
  }
  #graphControls button:hover { border-color: #3b82f6; color: #3b82f6; }
</style>
</head>
<body class="min-h-screen">
<script id="cairn-data" type="application/json">__DATA_JSON__</script>

<header class="border-b" style="border-color: var(--border-color); background-color: var(--header-bg);">
  <div class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between flex-wrap gap-2">
    <div>
      <h1 class="text-xl font-bold">Cairn: Decision Memory</h1>
      <p class="text-xs text-gray-400">Search here before re-arguing a call your team already made. Decisions with no recorded reasoning are flagged below, those are worth revisiting. Generated <span id="genAt"></span></p>
    </div>
    <div class="flex items-center gap-6">
      <div id="projectSwitcherWrap" style="position:relative; display:none;">
        <button id="projectSwitcherBtn">
          🗂 <span id="projectSwitcherLabel"></span> <span>&#9662;</span>
        </button>
        <div id="projectSwitcherMenu" class="hidden"></div>
      </div>
      <button id="themeToggle" class="px-3 py-1.5 rounded-lg border text-xs font-medium cursor-pointer transition-colors" style="border-color: var(--border-color); background-color: var(--card-bg); color: var(--text-main);">
        🌓 Toggle Theme
      </button>
      <div class="flex gap-4 text-sm" id="headerCounts"></div>
    </div>
  </div>
</header>

<div id="toolbar">
  <div class="max-w-6xl mx-auto px-6" id="scopeTabsWrap" style="display:none;">
    <div id="scopeTabs"></div>
  </div>
  <div class="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between flex-wrap gap-3">
    <div class="flex items-center gap-3 flex-wrap">
      <div class="search-wrap">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input id="searchInput" type="text" placeholder="Search decisions...">
      </div>
      <div class="toolbar-cluster">
        <span class="toolbar-label">Status</span>
        <div class="toolbar-group">
          <button class="filter-btn active" data-filter="all">All</button>
          <button class="filter-btn" data-filter="active">Active</button>
          <button class="filter-btn" data-filter="invalidated">Invalidated</button>
          <button class="filter-btn" data-filter="recent">Last 14d</button>
          <button class="filter-btn" data-filter="needs-docs" title="Decisions with no recorded reasoning">Needs docs</button>
        </div>
      </div>
      <span id="resultCount"></span>
    </div>
    <div class="toolbar-cluster" style="align-items: flex-end;">
      <span class="toolbar-label">View</span>
      <div class="toolbar-group">
        <button id="viewGraphBtn" class="view-btn active">Graph</button>
        <button id="viewCardsBtn" class="view-btn">Cards</button>
      </div>
    </div>
  </div>
</div>

<section class="max-w-6xl mx-auto px-6 pb-6" id="graphSection">
  <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
    <div>
      <h2 class="text-sm uppercase text-gray-500">Decision Map<span id="graphScopeLabel"></span></h2>
      <p class="text-xs text-gray-500 mt-0.5">Each node: one recorded decision.</p>
    </div>
    <div class="graph-legend text-xs text-gray-500">
      <span title="Reasoning + alternatives + evidence + supersedes, all recorded"><span class="dot" style="background:#059669"></span>4/4 well-documented</span>
      <span><span class="dot" style="background:#2563eb"></span>3/4</span>
      <span><span class="dot" style="background:#ea580c"></span>2/4</span>
      <span><span class="dot" style="background:#dc2626"></span>1/4</span>
      <span title="No reasoning recorded, click Needs docs to find these"><span class="dot" style="background:#4b5563"></span>0/4 undocumented</span>
    </div>
  </div>
  <div id="needsDocsBanner" class="hidden text-xs mb-3 px-3 py-2 rounded-lg" style="background: rgba(220,38,38,0.08); border: 1px solid rgba(220,38,38,0.3); color: #dc2626;">
    <span id="needsDocsText"></span>
    <button id="needsDocsBtn" style="text-decoration: underline; cursor: pointer; margin-left: 4px; color: inherit;">Show them</button>
  </div>
  <div id="graphContainer" class="rounded-lg border" style="border-color: var(--border-color); position: relative; overflow: hidden; height: 540px;">
    <svg id="graphSvg" width="100%" height="100%"></svg>
    <div id="graphEmpty" class="hidden absolute inset-0 flex items-center justify-center text-sm text-gray-500 text-center px-6">
      No decisions recorded yet. record_decision writes the first node.
    </div>
    <div id="graphControls">
      <button id="zoomInBtn" title="Zoom in">+</button>
      <button id="zoomOutBtn" title="Zoom out">&minus;</button>
      <button id="zoomResetBtn" title="Reset view">&#8634;</button>
    </div>
  </div>
  <p class="text-xs text-gray-500 mt-2">Node size = version count. Color = audit-score completeness. Arrows point from a decision to what superseded it. Hover a node for its label and connections, drag to reposition, double-click to pin in place, scroll or use the +/&minus; controls to zoom, click for full provenance.</p>
</section>

<section class="max-w-6xl mx-auto px-6 pb-12" id="cardsSection" style="display:none;">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm uppercase text-gray-500">Decisions<span id="cardsScopeLabel"></span></h2>
    <div class="text-xs text-gray-400">Click a card for full provenance</div>
  </div>
  <div id="grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"></div>
  <div id="cardsEmpty" class="hidden text-sm text-gray-500 text-center py-12">No decisions match the current search/filters.</div>
</section>

<section class="max-w-6xl mx-auto px-6 py-6">
  <h2 class="text-sm uppercase text-gray-500 mb-2">Activity (30 days, this project)</h2>
  <canvas id="activity" height="60"></canvas>
</section>

<div id="modal" class="fixed inset-0 hidden items-center justify-center p-4 modal-backdrop z-40">
  <div class="max-w-2xl w-full max-h-[85vh] overflow-y-auto rounded-lg p-6 modal-content">
    <div class="flex justify-between items-start mb-4">
      <h3 id="m_title" class="text-lg font-semibold pr-4"></h3>
      <button onclick="closeModal()" class="text-gray-400 hover:text-white text-2xl leading-none cursor-pointer">&times;</button>
    </div>
    <div id="m_body" class="space-y-4 text-sm"></div>
  </div>
</div>

<footer class="text-center text-xs text-gray-500 py-6 border-t" style="border-color: var(--border-color);">
  Generated by Cairn. No server, no telemetry, just an HTML file.
</footer>

<script>
const data = JSON.parse(document.getElementById('cairn-data').textContent);
document.getElementById('genAt').textContent = data.generated_at;

const headerCounts = document.getElementById('headerCounts');
headerCounts.innerHTML = data.has_global
  ? `<div><span class="text-green-500 font-semibold">${data.project_active_count}</span> <span class="text-gray-400">project active</span></div>
     <div><span class="text-blue-400 font-semibold">${data.global_active_count}</span> <span class="text-gray-400">global active</span></div>
     <div><span class="text-gray-400">${data.invalidated_count} invalidated</span></div>`
  : `<div><span class="text-green-500 font-semibold">${data.active_count}</span> <span class="text-gray-400">active</span></div>
     <div><span class="text-gray-400">${data.invalidated_count} invalidated</span></div>`;

// ------------------------- project switcher -------------------------
// Lists sibling Cairn projects registered on this machine (~/.cairn/projects.json).
// Never merges their memory data: each entry just links to that project's own
// dashboard.html, generated independently by `cairn memory dashboard` there.

if (data.other_projects && data.other_projects.length) {
  const wrap = document.getElementById('projectSwitcherWrap');
  const btn = document.getElementById('projectSwitcherBtn');
  const menu = document.getElementById('projectSwitcherMenu');
  wrap.style.display = '';
  document.getElementById('projectSwitcherLabel').textContent =
    `${data.other_projects.length} other project${data.other_projects.length === 1 ? '' : 's'}`;
  menu.innerHTML = `<div class="psm-label">Other Cairn projects on this machine</div>` +
    data.other_projects.map(p => {
      if (p.dashboard_uri) {
        return `<a href="${p.dashboard_uri}" target="_blank" rel="noopener">${escapeHtml(p.name)}<span class="psm-path">${escapeHtml(p.path)}</span></a>`;
      }
      return `<a class="disabled" title="Run 'cairn memory dashboard' there first">${escapeHtml(p.name)} (no dashboard yet)<span class="psm-path">${escapeHtml(p.path)}</span></a>`;
    }).join('');
  btn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    menu.classList.toggle('hidden');
  });
  document.addEventListener('click', () => menu.classList.add('hidden'));
}

// ------------------------- theme -------------------------

const getTheme = () => {
  if (localStorage.getItem('cairn-theme')) {
    return localStorage.getItem('cairn-theme');
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
};

const setTheme = (theme) => {
  if (theme === 'dark') {
    document.documentElement.classList.add('dark');
  } else {
    document.documentElement.classList.remove('dark');
  }
  localStorage.setItem('cairn-theme', theme);
  drawChart(theme === 'dark');
  buildGraph(theme === 'dark');
};

// ------------------------- activity chart -------------------------

const labels = data.activity.map(a => a.day);
const counts = data.activity.map(a => a.count);
let activityChart = null;

function drawChart(isDark) {
  const ctx = document.getElementById('activity');
  if (activityChart) {
    activityChart.destroy();
  }
  const tickColor = isDark ? '#9ca3af' : '#4b5563';
  const gridColor = isDark ? '#1f2937' : '#e5e7eb';
  activityChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: counts,
        backgroundColor: '#3b82f6',
        borderRadius: 4
      }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: tickColor },
          grid: { color: gridColor }
        },
        y: {
          ticks: { color: tickColor },
          grid: { color: gridColor }
        }
      },
    },
  });
}

// ------------------------- decision graph -------------------------
// Force-directed node-link graph (d3-force): nodes = decisions, edges =
// supersedes chains. Project and global decisions are never rendered on
// the same canvas at once: a scope tab (This Project / Global) picks
// which one set of decisions the graph and card views currently show, so
// the two never visually blend together. Status filter and search then
// narrow within whichever scope is active.

function drag(simulation) {
  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(event, d) {
    d.fx = event.x; d.fy = event.y;
  }
  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    if (!d.pinned) { d.fx = null; d.fy = null; }
  }
  return d3.drag().on('start', dragstarted).on('drag', dragged).on('end', dragended);
}

const SCORE_COLORS = ['#4b5563', '#dc2626', '#ea580c', '#2563eb', '#059669'];
let graphSimulation = null;
let graphLinks = [];
let currentZoom = null;
let currentNodeSel = null;
let currentLinkSel = null;
let currentFilter = 'all';
let currentQuery = '';
let isDarkNow = false;

const hasGlobal = data.has_global
  && data.decisions.some(d => d.scope === 'global')
  && data.decisions.some(d => d.scope === 'project');
let selectedScope = hasGlobal ? 'project' : 'all';

function scopedDecisions() {
  return hasGlobal ? data.decisions.filter(d => d.scope === selectedScope) : data.decisions;
}

function matchesStatus(d, filt) {
  if (filt === 'all') return true;
  if (filt === 'active') return d.active;
  if (filt === 'invalidated') return !d.active;
  if (filt === 'needs-docs') return (d.audit_score || 0) <= 1;
  if (filt === 'recent') {
    const cutoff = Date.now() - 14 * 24 * 60 * 60 * 1000;
    return new Date(d.timestamp).getTime() > cutoff;
  }
  return true;
}

function matchesAll(d) {
  if (!matchesStatus(d, currentFilter)) return false;
  if (currentQuery) {
    const q = currentQuery;
    const hay = `${d.decision || ''} ${d.reasoning || ''} ${d.chose_because || ''}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function applyFilters() {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === currentFilter));

  let matchCount = 0;
  if (currentNodeSel) {
    currentNodeSel.style('opacity', d => matchesAll(d) ? 1 : 0.06);
    currentNodeSel.select('text').style('opacity', 0);
  }
  if (currentLinkSel) {
    currentLinkSel.style('opacity', d => (matchesAll(d.source) && matchesAll(d.target)) ? 1 : 0.04);
  }
  const scoped = scopedDecisions();
  const scopedIds = new Set(scoped.map(d => String(d.id)));
  document.querySelectorAll('#grid .card').forEach(card => {
    const cd = JSON.parse(card.dataset.d);
    const ok = scopedIds.has(String(cd.id)) && matchesAll(cd);
    card.style.display = ok ? '' : 'none';
    if (ok) matchCount++;
  });
  document.getElementById('cardsEmpty').classList.toggle('hidden', matchCount > 0 || scoped.length === 0);
  const total = scoped.length;
  document.getElementById('resultCount').textContent = (currentFilter !== 'all' || currentQuery)
    ? `${matchCount} of ${total} shown` : `${total} total`;

  const needsDocsCount = scoped.filter(d => d.active && (d.audit_score || 0) <= 1).length;
  const banner = document.getElementById('needsDocsBanner');
  if (needsDocsCount > 0 && currentFilter !== 'needs-docs') {
    banner.classList.remove('hidden');
    document.getElementById('needsDocsText').textContent =
      `${needsDocsCount} active decision${needsDocsCount === 1 ? '' : 's'} in this view ${needsDocsCount === 1 ? 'has' : 'have'} little or no recorded reasoning.`;
  } else {
    banner.classList.add('hidden');
  }
}

document.getElementById('needsDocsBtn').addEventListener('click', () => { currentFilter = 'needs-docs'; applyFilters(); });
document.querySelectorAll('.filter-btn').forEach(b => b.addEventListener('click', () => { currentFilter = b.dataset.filter; applyFilters(); }));

if (hasGlobal) {
  const projectCount = data.decisions.filter(d => d.scope === 'project').length;
  const globalCount = data.decisions.filter(d => d.scope === 'global').length;
  document.getElementById('scopeTabsWrap').style.display = '';
  const tabs = document.getElementById('scopeTabs');
  tabs.innerHTML = `
    <button class="scope-tab active" data-scope="project">This Project <span class="n">${projectCount}</span></button>
    <button class="scope-tab" data-scope="global">Global, All Projects <span class="n">${globalCount}</span></button>`;
  document.getElementById('graphScopeLabel').textContent = ': This Project';
  document.getElementById('cardsScopeLabel').textContent = ': This Project';
  tabs.querySelectorAll('.scope-tab').forEach(b => b.addEventListener('click', () => {
    selectedScope = b.dataset.scope;
    tabs.querySelectorAll('.scope-tab').forEach(x => x.classList.toggle('active', x === b));
    const label = selectedScope === 'project' ? ': This Project' : ': Global';
    document.getElementById('graphScopeLabel').textContent = label;
    document.getElementById('cardsScopeLabel').textContent = label;
    buildGraph(isDarkNow);
    applyFilters();
  }));
}

document.getElementById('searchInput').addEventListener('input', (e) => {
  currentQuery = e.target.value.trim().toLowerCase();
  applyFilters();
});

function buildGraph(isDark) {
  isDarkNow = isDark;
  const container = document.getElementById('graphContainer');
  const width = container.clientWidth || 900;
  const height = container.clientHeight || 540;
  const svg = d3.select('#graphSvg').attr('viewBox', [0, 0, width, height]);
  svg.selectAll('*').remove();
  if (graphSimulation) { graphSimulation.stop(); graphSimulation = null; }

  const gridDefs = svg.append('defs').append('pattern')
    .attr('id', 'bgGrid').attr('width', 28).attr('height', 28).attr('patternUnits', 'userSpaceOnUse');
  gridDefs.append('path')
    .attr('d', 'M 28 0 L 0 0 0 28')
    .attr('fill', 'none')
    .attr('stroke', isDark ? '#1b2537' : '#e9edf3')
    .attr('stroke-width', 1);
  svg.append('rect').attr('width', width).attr('height', height).attr('fill', 'url(#bgGrid)');

  const scoped = scopedDecisions();
  document.getElementById('graphEmpty').classList.toggle('hidden', scoped.length > 0);
  if (scoped.length === 0) return;

  const nodes = scoped.map(d => ({ ...d }));
  const nodeById = new Map(nodes.map(n => [String(n.id), n]));
  const links = [];
  nodes.forEach(n => {
    if (n.superseded_by && nodeById.has(String(n.superseded_by))) {
      links.push({ source: n.id, target: n.superseded_by });
    }
  });
  graphLinks = links;

  svg.append('defs').append('marker')
    .attr('id', 'arrow').attr('viewBox', '0 -5 10 10').attr('refX', 20).attr('refY', 0)
    .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
    .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', isDark ? '#4b5563' : '#9ca3af');

  const g = svg.append('g');
  const zoomBehavior = d3.zoom()
    .scaleExtent([0.3, 4])
    .on('start', () => container.classList.add('panning'))
    .on('zoom', ev => g.attr('transform', ev.transform))
    .on('end', () => container.classList.remove('panning'));
  svg.call(zoomBehavior);
  currentZoom = zoomBehavior;

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(85).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-210))
    .force('x', d3.forceX(width / 2).strength(0.05))
    .force('y', d3.forceY(height / 2).strength(0.06))
    .force('collide', d3.forceCollide().radius(d => 14 + Math.min(d.version_count || 1, 5) * 2));
  graphSimulation = simulation;

  const linkSel = g.append('g')
    .attr('stroke', isDark ? '#4b5563' : '#9ca3af')
    .attr('stroke-width', 1.5)
    .selectAll('line').data(links).join('line')
    .attr('marker-end', 'url(#arrow)');
  currentLinkSel = linkSel;

  let nodeClickTimer = null;
  const nodeG = g.append('g').selectAll('g.graph-node').data(nodes).join('g')
    .attr('class', 'graph-node')
    .style('cursor', 'pointer')
    .on('click', (ev, d) => {
      if (nodeClickTimer) return;
      nodeClickTimer = setTimeout(() => { openModal(d); nodeClickTimer = null; }, 220);
    })
    .on('dblclick', function (ev, d) {
      clearTimeout(nodeClickTimer);
      nodeClickTimer = null;
      d.pinned = !d.pinned;
      if (d.pinned) { d.fx = d.x; d.fy = d.y; } else { d.fx = null; d.fy = null; }
      d3.select(this).classed('pinned', d.pinned);
    })
    .on('mouseenter', function (ev, d) {
      const neighbors = new Set([d.id]);
      graphLinks.forEach(l => {
        if (l.source.id === d.id) neighbors.add(l.target.id);
        if (l.target.id === d.id) neighbors.add(l.source.id);
      });
      nodeG.style('opacity', n => neighbors.has(n.id) ? 1 : 0.12);
      nodeG.select('text').style('opacity', n => neighbors.has(n.id) ? 1 : 0);
      linkSel.style('opacity', l => (l.source.id === d.id || l.target.id === d.id) ? 1 : 0.05);
    })
    .on('mouseleave', () => applyFilters())
    .call(drag(simulation));
  currentNodeSel = nodeG;

  nodeG.append('title')
    .text(d => `${d.decision}\\n${d.scope === 'global' ? 'Global' : 'This project'} \\u00b7 ${d.active ? 'active' : 'invalidated'} \\u00b7 audit ${d.audit_score}/4`);

  nodeG.append('circle')
    .attr('r', d => 8 + Math.min(d.version_count || 1, 5) * 2)
    .attr('fill', d => SCORE_COLORS[d.audit_score] || SCORE_COLORS[0])
    .attr('opacity', d => d.active ? 1 : 0.4)
    .attr('stroke', isDark ? '#0b1020' : '#ffffff')
    .attr('stroke-width', 2);

  nodeG.append('text')
    .text(d => (d.decision || '').slice(0, 30) + ((d.decision || '').length > 30 ? '\\u2026' : ''))
    .attr('font-size', 11)
    .attr('font-weight', 600)
    .attr('fill', isDark ? '#e5e7eb' : '#111827')
    .attr('paint-order', 'stroke')
    .attr('stroke', isDark ? '#0b1020' : '#ffffff')
    .attr('stroke-width', 3)
    .attr('text-anchor', 'middle')
    .attr('opacity', 0)
    .attr('dy', d => -(12 + Math.min(d.version_count || 1, 5) * 2));

  simulation.on('tick', () => {
    linkSel
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeG.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  applyFilters();
}

// ------------------------- view toggle -------------------------

function setView(view) {
  document.getElementById('graphSection').style.display = view === 'graph' ? '' : 'none';
  document.getElementById('cardsSection').style.display = view === 'cards' ? '' : 'none';
  document.getElementById('viewGraphBtn').classList.toggle('active', view === 'graph');
  document.getElementById('viewCardsBtn').classList.toggle('active', view === 'cards');
}
document.getElementById('viewGraphBtn').addEventListener('click', () => setView('graph'));
document.getElementById('viewCardsBtn').addEventListener('click', () => setView('cards'));

// ------------------------- graph zoom controls -------------------------

document.getElementById('zoomInBtn').addEventListener('click', () => {
  if (!currentZoom) return;
  d3.select('#graphSvg').transition().duration(200).call(currentZoom.scaleBy, 1.3);
});
document.getElementById('zoomOutBtn').addEventListener('click', () => {
  if (!currentZoom) return;
  d3.select('#graphSvg').transition().duration(200).call(currentZoom.scaleBy, 0.75);
});
document.getElementById('zoomResetBtn').addEventListener('click', () => {
  if (!currentZoom) return;
  d3.select('#graphSvg').transition().duration(300).call(currentZoom.transform, d3.zoomIdentity);
});

// ------------------------- initial render -------------------------

const initialTheme = getTheme();
setTheme(initialTheme);

document.getElementById('themeToggle').addEventListener('click', () => {
  const currentTheme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  setTheme(currentTheme === 'dark' ? 'light' : 'dark');
});

// ------------------------- card grid (secondary view) -------------------------

const grid = document.getElementById('grid');
data.decisions.forEach(d => {
  const card = document.createElement('div');
  card.className = 'card rounded-lg p-4';
  card.dataset.d = JSON.stringify({
    id: d.id, active: d.active, timestamp: d.timestamp, scope: d.scope,
    decision: d.decision, reasoning: d.reasoning, chose_because: d.chose_because,
    audit_score: d.audit_score,
  });
  card.onclick = () => openModal(d);
  const scopeTag = d.scope === 'global' ? '<span class="text-blue-400">global</span> \\u00b7 ' : '';
  card.innerHTML = `
    <div class="flex justify-between items-start mb-2">
      <div class="text-sm font-medium leading-snug">${escapeHtml(d.decision.slice(0,80))}</div>
      <span class="text-xs px-2 py-0.5 rounded badge-${d.audit_score}">${d.audit_score}/4</span>
    </div>
    <div class="text-xs text-gray-400 line-clamp-3">${escapeHtml((d.reasoning||'').slice(0,140))}</div>
    <div class="text-xs text-gray-500 mt-3 flex justify-between">
      <span>${scopeTag}${d.active ? '<span class="text-green-500">active</span>' : '<span>invalidated</span>'}</span>
      <span>v${d.version_count}</span>
    </div>
    <div class="text-xs text-gray-600 mt-1">${d.timestamp.slice(0,10)}</div>
  `;
  grid.appendChild(card);
});
applyFilters();

function openModal(d) {
  document.getElementById('m_title').textContent = d.decision;
  const parts = [];
  const scopeNote = d.scope === 'global' ? ' \\u00b7 global (visible in every project)' : '';
  parts.push(section('Decision', `<div>${escapeHtml(d.decision)}</div>
    <div class="text-xs text-gray-500 mt-1">importance ${d.importance} \\u00b7 recorded ${d.timestamp}${scopeNote}</div>`));
  if (d.reasoning) parts.push(section('Reasoning', `<div>${escapeHtml(d.reasoning)}</div>`));
  if (d.alternatives && d.alternatives.length) {
    const list = d.alternatives.map(a => `<li><strong>${escapeHtml(a.option||'')}</strong>: ${escapeHtml(a.rejected_because||'')}</li>`).join('');
    parts.push(section('Alternatives considered', `<ul class="list-disc pl-5 space-y-1">${list}</ul>`));
  }
  if (d.evidence && d.evidence.length) {
    const list = d.evidence.map(e => `<li><strong>${escapeHtml(e.source||'')}</strong>: ${escapeHtml(e.claim||'')}</li>`).join('');
    parts.push(section('Evidence', `<ul class="list-disc pl-5 space-y-1">${list}</ul>`));
  }
  if (d.superseded_by) parts.push(section('Timeline', `<div>Superseded by #${d.superseded_by}</div>`));
  parts.push(section('Helper LLM commentary', `<div class="text-gray-400 italic">None recorded. LLM commentary is shown labeled when present.</div>`));
  document.getElementById('m_body').innerHTML = parts.join('');
  document.getElementById('modal').classList.remove('hidden');
  document.getElementById('modal').classList.add('flex');
}
function closeModal() {
  document.getElementById('modal').classList.add('hidden');
  document.getElementById('modal').classList.remove('flex');
}
function section(title, body) {
  return `<div><div class="text-xs uppercase text-gray-500 mb-1">${title}</div>${body}</div>`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
</script>
</body>
</html>
"""
