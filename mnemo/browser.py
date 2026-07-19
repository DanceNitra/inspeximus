"""mnemo.browser — a zero-dependency, offline memory browser.

Every competitor ships a console/inspector to SEE what the agent remembers; mnemo had none. This renders the store
into a SINGLE self-contained HTML file (all data inlined, vanilla JS, inline CSS — no server, no build, works
offline, opens in any browser). Shows each memory's text, type, tags, value, status (active/superseded), key, and
time, with client-side search + filters, plus a header summary (counts, cohorts, contradictions). Read-only by
design (viewing, not editing — edits go through the audited remember/revert/forget path).

Usage:
    from mnemo import Mnemo
    from mnemo.browser import render_html, write_html
    write_html(Mnemo("memory.json"), "mnemo_browser.html")   # then open the file
or via the CLI: `mnemo browse [--out FILE] [--open]`.
"""
from __future__ import annotations
import json, html, datetime


def _rows(store):
    rows = []
    for r in getattr(store, "items", []):
        rows.append({
            "id": r.get("id", ""),
            "text": r.get("text", ""),
            "mtype": r.get("mtype", ""),
            "tags": r.get("tags") or [],
            "value": round(float(r.get("value", 0) or 0), 2),
            "status": r.get("status", "active"),
            "key": r.get("key") or "",
            "iso": r.get("iso") or "",
            "kind": (r.get("meta") or {}).get("kind", ""),
        })
    return rows


def _summary(store, rows):
    active = [x for x in rows if x["status"] != "superseded"]
    try:
        contra = len(store.contradictions())
    except Exception:
        contra = None
    try:
        cohorts = store.value_by_cohort()
    except Exception:
        cohorts = {}
    return {"total": len(rows), "active": len(active),
            "superseded": len(rows) - len(active), "contradictions": contra,
            "cohorts": cohorts}


_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mnemo memory browser</title>
<style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#59636e;--border:#d1d9e0;--card:#f6f8fa;--accent:#0d7d84;--stale:#cf222e}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--border:#30363d;--card:#161b22;--accent:#39c5bb;--stale:#f85149}}}}
*{{box-sizing:border-box}}body{{margin:0;font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}}
header{{padding:16px 20px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg);z-index:2}}
h1{{margin:0 0 4px;font-size:18px}}h1 small{{color:var(--muted);font-weight:400}}
.stats{{color:var(--muted);font-size:13px;margin-top:6px}}.stats b{{color:var(--fg)}}
.controls{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}
input,select{{padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--fg);font-size:13px}}
input[type=search]{{flex:1;min-width:180px}}
main{{padding:14px 20px;display:grid;gap:10px;max-width:1100px;margin:0 auto}}
.card{{border:1px solid var(--border);border-radius:8px;padding:12px 14px;background:var(--card);overflow-wrap:anywhere}}
.card.stale{{opacity:.6}}
.meta{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px;font-size:12px;color:var(--muted)}}
.tag{{background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:1px 8px}}
.badge{{border-radius:4px;padding:1px 6px;font-weight:600}}
.badge.active{{color:var(--accent)}}.badge.superseded{{color:var(--stale)}}
.key{{font-family:ui-monospace,monospace;color:var(--accent)}}
.empty{{color:var(--muted);text-align:center;padding:40px}}
</style></head><body>
<header>
<h1>mnemo <small>memory browser &mdash; read-only, offline</small></h1>
<div class="stats">{stats}</div>
<div class="controls">
<input type="search" id="q" placeholder="search text / key / tag&hellip;">
<select id="type"><option value="">all types</option><option>episodic</option><option>semantic</option><option>procedural</option></select>
<select id="status"><option value="">all</option><option value="active">active only</option><option value="superseded">superseded only</option></select>
</div></header>
<main id="list"></main>
<script>
const DATA={data};
const esc=s=>String(s==null?"":s).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const list=document.getElementById('list');
function render(){{
 const q=document.getElementById('q').value.toLowerCase();
 const ty=document.getElementById('type').value, st=document.getElementById('status').value;
 const rows=DATA.filter(r=>{{
  if(ty&&r.mtype!==ty)return false;
  if(st&&(r.status||'active')!==st)return false;
  if(q){{const hay=(r.text+' '+r.key+' '+(r.tags||[]).join(' ')).toLowerCase();if(!hay.includes(q))return false;}}
  return true;}});
 list.innerHTML=rows.length?rows.map(r=>{{
  const stale=(r.status==='superseded');
  const tags=(r.tags||[]).map(t=>`<span class="tag">${{esc(t)}}</span>`).join('');
  return `<div class="card ${{stale?'stale':''}}"><div>${{esc(r.text)}}</div>
   <div class="meta"><span class="badge ${{stale?'superseded':'active'}}">${{esc(r.status||'active')}}</span>
   ${{r.mtype?`<span>${{esc(r.mtype)}}</span>`:''}}${{r.kind?`<span>&middot; ${{esc(r.kind)}}</span>`:''}}
   ${{r.key?`<span class="key">${{esc(r.key)}}</span>`:''}}<span>val ${{r.value}}</span>
   ${{r.iso?`<span>${{esc(r.iso).slice(0,10)}}</span>`:''}}${{tags}}</div></div>`;
 }}).join(''):'<div class="empty">no memories match</div>';
}}
['q','type','status'].forEach(id=>document.getElementById(id).addEventListener('input',render));
render();
</script></body></html>"""


def render_html(store) -> str:
    """Return a single self-contained HTML string for the store (all data inlined; opens offline)."""
    rows = _rows(store)
    s = _summary(store, rows)
    coh = ", ".join(f"{html.escape(str(k))} ({v.get('count', 0)})" for k, v in list(s["cohorts"].items())[:8])
    stats = (f"<b>{s['total']}</b> memories &middot; <b>{s['active']}</b> active &middot; "
             f"<b>{s['superseded']}</b> superseded &middot; contradictions: <b>{s['contradictions']}</b>"
             + (f"<br>cohorts: {coh}" if coh else ""))
    # The rows are inlined into an inline <script> block, and json.dumps does NOT escape < > & — so a memory
    # whose text/key/tag contains "</script>" CLOSES the script element and everything after it is parsed as
    # HTML (stored XSS, running in the opened file:// document). The JS-side esc() never gets a chance: the
    # breakout happens at parse time, before any script runs. Memory text is exactly what agents ingest from
    # tools, web pages and MCP callers, so this is reachable through ordinary use. \uXXXX is valid inside a
    # JSON string and parses to the identical character, so the data is unchanged — only the transport is safe.
    data = (json.dumps(rows, default=str)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return _TEMPLATE.format(stats=stats, data=data)


def write_html(store, path: str = "mnemo_browser.html") -> str:
    """Render + write the browser to `path`; returns the path."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(store))
    return path
