"""cairn memory dashboard, single-file HTML view of project memory.

No server. No build step. One Python module that collects decisions from the
project's SQLite memory and renders a self-contained HTML file with a card
grid, modal drill-down, and 30-day activity heatmap. Tailwind / Chart.js /
vis-network are pulled from CDNs at view time.

Generated file lives at `.cairn/persistence/dashboard.html`.
"""
from __future__ import annotations

import json
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import typer

from cairn.memory.memory import sqlite_connect


FILTERS = {"all", "active", "invalidated", "recent"}


def collect_dashboard_data(db_path: Path, filt: str = "all") -> dict[str, Any]:
    """Read decisions from SQLite and produce the JSON data island."""
    if filt not in FILTERS:
        filt = "all"
    if not db_path.exists():
        return _empty_data(filt)

    with sqlite_connect(db_path) as conn:
        decisions = _load_decisions(conn, filt)
        activity = _load_activity(conn)
        active_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NULL"
        ).fetchone()[0]
        invalidated_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NOT NULL"
        ).fetchone()[0]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": filt,
        "active_count": active_count,
        "invalidated_count": invalidated_count,
        "activity": activity,
        "decisions": decisions,
    }


def _empty_data(filt: str) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": filt,
        "active_count": 0,
        "invalidated_count": 0,
        "activity": [],
        "decisions": [],
    }


def _load_decisions(conn: sqlite3.Connection, filt: str) -> list[dict]:
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

    out = []
    for r in rows:
        evidence = [
            {"source": s, "claim": c}
            for s, c in conn.execute(
                "SELECT source, claim FROM memory_evidence WHERE memory_id=?", (r[0],)
            ).fetchall()
        ]
        alts = json.loads(r[4]) if r[4] else []
        version_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE superseded_by=? OR id=?", (r[0], r[0])
        ).fetchone()[0]
        score = _audit_score(r[3], alts, evidence, r[5])
        out.append({
            "id": r[0],
            "timestamp": r[1],
            "decision": r[2],
            "reasoning": r[3] or "",
            "alternatives": alts,
            "evidence": evidence,
            "superseded_by": r[5],
            "chose_because": r[6] or "",
            "active": r[7] is None,
            "importance": r[8] or 1,
            "version_count": version_count,
            "audit_score": score,
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


def write_dashboard(project_dir: Path, filt: str = "all") -> Path:
    """Generate the dashboard HTML at .cairn/persistence/dashboard.html."""
    from cairn.config.loader import load_config
    config = load_config()
    db_path = Path(
        (config.get("memory", {}) or {}).get("db_path", "~/.cairn/memory.db")
    ).expanduser()
    data = collect_dashboard_data(db_path, filt=filt)
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
    db_path = Path(
        (config.get("memory", {}) or {}).get("db_path", "~/.cairn/memory.db")
    ).expanduser()
    data = collect_dashboard_data(db_path, filt=filt)
    if fmt == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    out = write_dashboard(Path.cwd(), filt=filt)
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
</style>
</head>
<body class="min-h-screen">
<script id="cairn-data" type="application/json">__DATA_JSON__</script>

<header class="border-b" style="border-color: var(--border-color); background-color: var(--header-bg);">
  <div class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
    <div>
      <h1 class="text-xl font-bold">Cairn, Decision Memory</h1>
      <p class="text-xs text-gray-400">Generated <span id="genAt"></span> · Filter: <span id="filterLbl"></span></p>
    </div>
    <div class="flex items-center gap-6">
      <button id="themeToggle" class="px-3 py-1.5 rounded-lg border text-xs font-medium cursor-pointer transition-colors" style="border-color: var(--border-color); background-color: var(--card-bg); color: var(--text-main);">
        🌓 Toggle Theme
      </button>
      <div class="flex gap-4 text-sm">
        <div><span class="text-green-500 font-semibold" id="activeCount"></span> active</div>
        <div><span class="text-gray-400" id="invalidatedCount"></span> invalidated</div>
      </div>
    </div>
  </div>
</header>

<section class="max-w-6xl mx-auto px-6 py-6">
  <h2 class="text-sm uppercase text-gray-500 mb-2">Activity (30 days)</h2>
  <canvas id="activity" height="60"></canvas>
</section>

<section class="max-w-6xl mx-auto px-6 pb-12">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm uppercase text-gray-500">Decisions</h2>
    <div class="text-xs text-gray-400">Click a card for full provenance</div>
  </div>
  <div id="grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"></div>
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
  Generated by Cairn &mdash; no server, no telemetry, just an HTML file.
</footer>

<script>
const data = JSON.parse(document.getElementById('cairn-data').textContent);
document.getElementById('genAt').textContent = data.generated_at;
document.getElementById('filterLbl').textContent = data.filter;
document.getElementById('activeCount').textContent = data.active_count;
document.getElementById('invalidatedCount').textContent = data.invalidated_count;

// Theme toggle logic
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
};

// activity heatmap (simple bar chart)
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

// Initial theme setup
const initialTheme = getTheme();
setTheme(initialTheme);

document.getElementById('themeToggle').addEventListener('click', () => {
  const currentTheme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  setTheme(currentTheme === 'dark' ? 'light' : 'dark');
});

// card grid
const grid = document.getElementById('grid');
data.decisions.forEach(d => {
  const card = document.createElement('div');
  card.className = 'card rounded-lg p-4';
  card.onclick = () => openModal(d);
  card.innerHTML = `
    <div class="flex justify-between items-start mb-2">
      <div class="text-sm font-medium leading-snug">${escapeHtml(d.decision.slice(0,80))}</div>
      <span class="text-xs px-2 py-0.5 rounded badge-${d.audit_score}">${d.audit_score}/4</span>
    </div>
    <div class="text-xs text-gray-400 line-clamp-3">${escapeHtml((d.reasoning||'').slice(0,140))}</div>
    <div class="text-xs text-gray-500 mt-3 flex justify-between">
      <span>${d.active ? '<span class="text-green-500">active</span>' : '<span>invalidated</span>'}</span>
      <span>v${d.version_count}</span>
    </div>
    <div class="text-xs text-gray-600 mt-1">${d.timestamp.slice(0,10)}</div>
  `;
  grid.appendChild(card);
});

function openModal(d) {
  document.getElementById('m_title').textContent = d.decision;
  const parts = [];
  parts.push(section('Decision', `<div>${escapeHtml(d.decision)}</div>
    <div class="text-xs text-gray-500 mt-1">importance ${d.importance} &middot; recorded ${d.timestamp}</div>`));
  if (d.reasoning) parts.push(section('Reasoning', `<div>${escapeHtml(d.reasoning)}</div>`));
  if (d.alternatives && d.alternatives.length) {
    const list = d.alternatives.map(a => `<li><strong>${escapeHtml(a.option||'')}</strong> &mdash; ${escapeHtml(a.rejected_because||'')}</li>`).join('');
    parts.push(section('Alternatives considered', `<ul class="list-disc pl-5 space-y-1">${list}</ul>`));
  }
  if (d.evidence && d.evidence.length) {
    const list = d.evidence.map(e => `<li><strong>${escapeHtml(e.source||'')}</strong>: ${escapeHtml(e.claim||'')}</li>`).join('');
    parts.push(section('Evidence', `<ul class="list-disc pl-5 space-y-1">${list}</ul>`));
  }
  if (d.superseded_by) parts.push(section('Timeline', `<div>Superseded by #${d.superseded_by}</div>`));
  parts.push(section('Helper LLM commentary', `<div class="text-gray-400 italic">(none recorded &mdash; LLM commentary is shown labeled when present)</div>`));
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
