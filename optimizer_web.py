#!/usr/bin/env python3
"""
Browser-based FPL transfer optimizer UI.

macOS ships a deprecated Tk 8.5 that renders blank Tkinter windows, and no
Homebrew Python here has tkinter at all. So instead of a desktop window, this
serves the SAME optimizer engine as a local web page using only the Python
standard library — no installs, runs on /usr/bin/python3.

    cd /Users/edbeesley/world-cup-fantasy/fpl_project
    /usr/bin/python3 optimizer_web.py

Then open http://127.0.0.1:8765 in your browser.

It reuses optimizer_app.py (engine, FPL API, Supabase loader) and
optimize_from_csv.py (CSV parsing + name matching) unchanged, so the numbers
are identical to the CLI. Nothing new is calculated here — this is only a view.
"""
import os
import sys
import html
import traceback
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import optimizer_app as O
import optimize_from_csv as C

HOST = "127.0.0.1"
PORT = int(os.environ.get("OPT_PORT", "8765"))

DEFAULT_CSV = "/Users/edbeesley/Downloads/TransferAlgorithm.csv"


# ---------------------------------------------------------------------------
# The core run: identical logic to optimize_from_csv.main(), but returns a
# structured result instead of printing, so the web layer can render it.
# ---------------------------------------------------------------------------
def run_optimization(manager_id, csv_path, use_supabase, transfers, horizon,
                     decay, free_override, log, budget_secs=300, method_override=None):
    log(f"Fetching FPL data for manager {manager_id}…")
    bs = O.get_bootstrap()
    mgr, squad_ids, purchase, bank, ft, next_gw = O.get_manager_squad(manager_id, bs)

    unmatched = []
    if use_supabase:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not (url and key):
            url2, key2 = O.load_env()
            url = url or url2
            key = key or key2
        if not (url and key):
            raise RuntimeError("Supabase mode needs SUPABASE_URL and SUPABASE_SERVICE_KEY "
                               "(set them in the environment or in a .env file in this folder).")
        log("Loading projections from Supabase (final_projections)…")
        players, gws, latest_gw = O.load_players(url, key, bs, next_gw, horizon)
        with_proj = sum(1 for p in players.values() if p.projections)
        source_label = f"Supabase final_projections (upload GW{latest_gw}, {with_proj} players with data)"
    else:
        if not csv_path or not os.path.exists(csv_path):
            raise RuntimeError(f"CSV not found: {csv_path!r}")
        log("Parsing CSV projections + name-matching…")
        players, all_gws, unmatched = C.load_csv_projections(csv_path, bs)
        gws = all_gws[:horizon]
        source_label = f"CSV {csv_path}"

    if not gws:
        raise RuntimeError("No gameweek projections found in the chosen source.")

    by_id = {e['id']: e for e in bs['elements']}
    tshort = {t['id']: t['short_name'] for t in bs['teams']}
    for eid in squad_ids:
        if eid not in players:
            e = by_id.get(eid)
            if e:
                players[eid] = O.Player(eid, e['web_name'], e['element_type'], e['team'],
                                        tshort.get(e['team'], '?'), e['now_cost'])
        p = players[eid]
        p.in_squad = True
        p.purchase_price = purchase.get(eid, p.now_cost)
        p.selling_price = O.selling_price(p.purchase_price, p.now_cost)

    free = free_override if free_override is not None else ft
    missing = [players[eid] for eid in squad_ids if not players[eid].projections]
    base_twxp = O.squad_twxp([players[i] for i in squad_ids], gws, decay)

    # Choose the search method via the time-budget planner (unless forced).
    if method_override == 'exhaustive':
        plan = {"method": "exhaustive", "depth": transfers, "beam_width": None,
                "pool": None, "est_secs": O._EXHAUSTIVE_SECS.get(transfers, 210),
                "note": "forced exhaustive"}
    elif method_override == 'beam':
        plan = O.plan_search(transfers, budget_secs, prefer_exhaustive=False)
    else:
        plan = O.plan_search(transfers, budget_secs, prefer_exhaustive=True)

    log(f"Search plan: {plan['method'].upper()} at depth {transfers}. {plan['note']}")
    log(f"Estimated time ~{plan['est_secs']:.0f}s (budget {budget_secs:.0f}s).")

    import time as _t
    t0 = _t.time()
    if plan["method"] == "exhaustive":
        base, ranked = O.optimize(squad_ids, players, gws, decay, bank, free,
                                  max_transfers=transfers, top_n=40,
                                  use_prune=False,
                                  progress_cb=lambda s: log("  " + s))
    else:
        base, ranked = O.beam_optimize(squad_ids, players, gws, decay, bank, free,
                                       max_transfers=transfers,
                                       beam_width=plan["beam_width"],
                                       pool_per_pos=plan["pool"], top_n=40,
                                       progress_cb=lambda s: log("  " + s))
    actual = _t.time() - t0
    log(f"Done in {actual:.0f}s.")

    by_depth = {}
    for m in ranked:
        by_depth.setdefault(len(m.transfers), []).append(m)

    return {
        "mgr": mgr, "bank": bank, "free": free, "next_gw": next_gw,
        "source_label": source_label, "gws": gws, "decay": decay,
        "unmatched": unmatched, "missing": missing, "players": players,
        "base_twxp": base_twxp, "by_depth": by_depth,
        "plan": plan, "actual_secs": actual, "transfers": transfers,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s))


def render_gw_grid(players, m, gws):
    """Per-GW: formation, total, captain, full XI with each player's points,
    and the bench. This is the detailed breakdown."""
    sq = [players[i] for i in m.squad_ids]
    rows = []
    for gw in gws:
        total, formation, starters, cap, bench = O.best_xi_detail(sq, gw)
        d, mm, f = formation
        xi_cells = []
        for p, v in starters:
            is_cap = p.element_id == cap[0].element_id
            tag = " (C)" if is_cap else ""
            shown = v * 2 if is_cap else v
            cls = "cap" if is_cap else ""
            xi_cells.append(
                f'<span class="pl {cls}">{esc(p.web_name)}{tag} '
                f'<b>{shown:.2f}</b></span>'
            )
        bench_cells = ", ".join(f"{esc(p.web_name)} {v:.2f}" for p, v in bench)
        rows.append(
            f'<tr><td class="gw">GW{gw}</td>'
            f'<td class="form">{d}-{mm}-{f}</td>'
            f'<td class="tot"><b>{total:.2f}</b></td>'
            f'<td class="cap-name">{esc(cap[0].web_name)} '
            f'({cap[1]:.2f}&rarr;{cap[1]*2:.2f})</td>'
            f'<td class="xi">{" ".join(xi_cells)}</td>'
            f'<td class="bench">{bench_cells}</td></tr>'
        )
    return (
        '<table class="grid"><thead><tr>'
        '<th>GW</th><th>Form</th><th>Total</th><th>Captain</th>'
        '<th>Starting XI (points each)</th><th>Bench</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
    )


def render_result(res):
    mgr = res["mgr"]
    gws = res["gws"]
    free = res["free"]
    players = res["players"]
    by_depth = res["by_depth"]

    parts = []
    parts.append('<div class="card">')
    parts.append(f'<h2>{esc(mgr["team_name"])} <span class="muted">· {esc(mgr["name"])}</span></h2>')
    parts.append(
        f'<p class="meta">Bank £{res["bank"]/10:.1f}m &nbsp;·&nbsp; '
        f'Free transfers: <b>{free}</b> &nbsp;·&nbsp; Planning GW{res["next_gw"]}<br>'
        f'Source: {esc(res["source_label"])}<br>'
        f'Horizon: GW{gws[0]}–GW{gws[-1]} ({len(gws)} weeks, decay {res["decay"]})<br>'
        f'Current squad time-weighted xP: <b>{res["base_twxp"]:.2f}</b></p>'
    )
    if res["missing"]:
        names = ", ".join(f'{esc(p.web_name)} ({esc(p.team_short)})' for p in res["missing"])
        parts.append(f'<p class="warn">⚠ Squad players with NO projection (scored 0): {names}</p>')
    if res["unmatched"]:
        parts.append(f'<p class="warn">⚠ {len(res["unmatched"])} CSV names did not match an FPL player.</p>')
    parts.append('</div>')

    # Search-method banner — honest about exact vs approximate
    plan = res.get("plan")
    if plan:
        if plan["method"] == "exhaustive":
            desc = ("<b>Exhaustive</b> search — every legal combination tried, "
                    "so this is the <b>guaranteed best</b> plan.")
            cls = "ok"
        else:
            desc = (f"<b>Beam</b> search (width {plan['beam_width']}) — a fast, "
                    "near-exact search. Wider beams (bigger time budget) get "
                    "closer to guaranteed-best; this is not provably optimal.")
            cls = "warn"
        parts.append(
            f'<div class="card"><p class="{cls}">{desc}<br>'
            f'<span class="muted">Depth {res.get("transfers","?")} · '
            f'estimated ~{plan["est_secs"]:.0f}s · actual '
            f'{res.get("actual_secs",0):.0f}s</span></p></div>'
        )

    # Marginal-gain curve — where does each extra transfer stop being worth it?
    parts.append('<div class="card"><h3>Marginal gain per transfer</h3>')
    parts.append('<table class="summary"><thead><tr><th>Depth</th><th>Best net</th>'
                 '<th>Marginal vs. one fewer</th><th>Note</th></tr></thead><tbody>')
    prev_net = 0.0
    for depth in sorted(d for d in by_depth if d > 0):
        best = by_depth[depth][0]
        marginal = best.net - prev_net
        note = f"incl −{(depth-free)*O.HIT_COST} hit" if depth > free else ""
        flat = ' style="color:#f7b955"' if (depth > 1 and marginal < 1.0) else ""
        parts.append(
            f'<tr{flat}><td>{depth}</td><td>{best.net:+.2f}</td>'
            f'<td>{marginal:+.2f}</td><td class="muted">{note}</td></tr>'
        )
        prev_net = best.net
    parts.append('</tbody></table>')
    parts.append('<p class="muted">Marginal = extra net points vs the best plan one '
                 'transfer shallower. Small or negative means that extra transfer '
                 'isn\'t earning its place.</p></div>')

    # Trade-off summary
    parts.append('<div class="card"><h3>Trade-off summary (best plan at each depth)</h3>')
    parts.append('<table class="summary"><thead><tr><th>Depth</th><th>Raw Δ (8wk)</th>'
                 '<th>Net (after hits)</th><th>Move</th></tr></thead><tbody>')
    parts.append('<tr><td>Keep squad</td><td>0.00</td><td>0.00</td><td>—</td></tr>')
    for depth in sorted(d for d in by_depth if d > 0):
        best = by_depth[depth][0]
        mv = " + ".join(f"{esc(o.web_name)}→{esc(n.web_name)}" for o, n in best.transfers)
        parts.append(
            f'<tr><td>{depth} transfer{"s" if depth>1 else ""}</td>'
            f'<td>+{best.delta:.2f}</td><td>+{best.net:.2f}</td><td>{mv}</td></tr>'
        )
    parts.append('</tbody></table>')
    parts.append('<p class="muted">Raw = expected-points gain over the horizon. '
                 'Net = after any −4 hits. You decide whether each extra transfer is worth it.</p></div>')

    # Per depth: options list + detailed grid for the best plan at that depth
    for depth in sorted(d for d in by_depth if d > 0):
        plans = by_depth[depth][:8]
        hitnote = "" if depth <= free else (
            f' <span class="muted">(uses {depth-free} beyond your {free} free → '
            f'−{(depth-free)*O.HIT_COST} hit)</span>')
        parts.append('<div class="card">')
        parts.append(f'<h3>Best {depth} transfer{"s" if depth>1 else ""}{hitnote}</h3>')
        parts.append('<table class="summary"><thead><tr><th>#</th><th>Raw Δ</th>'
                     '<th>Hit</th><th>Net</th><th>Move</th></tr></thead><tbody>')
        for i, m in enumerate(plans):
            mv = " + ".join(f"{esc(o.web_name)}→{esc(n.web_name)}" for o, n in m.transfers)
            parts.append(
                f'<tr><td>{i}</td><td>+{m.delta:.2f}</td>'
                f'<td>{("−"+str(m.hit)) if m.hit else "0"}</td>'
                f'<td>+{m.net:.2f}</td><td>{mv}</td></tr>'
            )
        parts.append('</tbody></table>')

        best = plans[0]
        out_in = "<br>".join(
            f'OUT {esc(o.web_name)} £{o.selling_price/10:.1f}m &nbsp;→&nbsp; '
            f'IN {esc(n.web_name)} £{n.now_cost/10:.1f}m'
            for o, n in best.transfers)
        parts.append(f'<h4>Detailed week-by-week for the best {depth}-transfer plan</h4>')
        parts.append(f'<p class="meta">{out_in}<br>Raw Δ {best.delta:+.2f} · '
                     f'Hit −{best.hit} · Net {best.net:+.2f}</p>')
        parts.append(render_gw_grid(players, best, gws))
        parts.append('</div>')

    return "".join(parts)


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>FPL Transfer Optimizer</title>
<style>
 body{font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;
   margin:0;background:#0f1419;color:#e6e6e6}
 header{background:#16202a;padding:14px 24px;border-bottom:1px solid #26313d}
 h1{margin:0;font-size:17px}
 main{max-width:1200px;margin:0 auto;padding:20px 24px}
 /* tab bar */
 #tabbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:18px}
 .tab{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px 8px 0 0;
   background:#16202a;border:1px solid #26313d;border-bottom:none;cursor:pointer;font-size:13px}
 .tab.active{background:#1d2a36;color:#fff;font-weight:600}
 .tab .glyph{font-size:12px}
 .tab .close{opacity:.5;font-size:14px;line-height:1}
 .tab .close:hover{opacity:1;color:#f77}
 #addtab{padding:8px 12px;border-radius:8px;background:#0f1419;border:1px dashed #3a4a5a;
   color:#9fb0c0;cursor:pointer;font-size:13px}
 #addtab:hover{border-color:#2f81f7;color:#cfe0f0}
 form{background:#16202a;padding:18px;border-radius:0 10px 10px 10px;margin-bottom:20px;
   display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
 label{display:flex;flex-direction:column;font-size:12px;color:#9fb0c0;gap:4px}
 input,select{padding:8px;border-radius:6px;border:1px solid #2c3a49;
   background:#0f1419;color:#e6e6e6;font-size:14px}
 .full{grid-column:1/3}
 button.run{grid-column:1/3;padding:12px;border:0;border-radius:8px;
   background:#2f81f7;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
 button.run:hover{background:#1f6feb}
 button.run:disabled{background:#2a3744;cursor:default}
 .card{background:#16202a;border:1px solid #26313d;border-radius:10px;
   padding:18px;margin-bottom:20px;overflow-x:auto}
 h2,h3,h4{margin:0 0 10px}
 .muted{color:#8a97a5;font-weight:400}
 .meta{color:#b8c4d0}
 .warn{color:#f7b955;background:#2a2313;padding:8px 12px;border-radius:6px}
 .ok{color:#7ee787;background:#13251a;padding:8px 12px;border-radius:6px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #26313d;
   vertical-align:top;white-space:nowrap}
 th{color:#9fb0c0;font-weight:600}
 table.grid td.xi{white-space:normal}
 .pl{display:inline-block;padding:2px 7px;margin:2px;border-radius:5px;background:#1d2a36}
 .pl.cap{background:#2f4a2f;color:#c9f5c9}
 .gw{font-weight:700}
 .tot b{color:#7ee787}
 pre.log{background:#0b0f14;color:#9fb0c0;padding:12px;border-radius:8px;
   white-space:pre-wrap;font-size:12px;max-height:260px;overflow:auto}
 .hidden{display:none}
 /* header + Transfer Algorithm status chip */
 header.hdr{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
 .ta{display:flex;align-items:center;gap:10px;padding:6px 6px 6px 12px;border-radius:10px;
   background:#0f1419;border:1px solid #26313d}
 .ta-dot{width:8px;height:8px;border-radius:50%;background:#475569;flex:none}
 .ta-dot.ta-ok{background:#10b981}
 .ta-dot.ta-warn{background:#f59e0b}
 .ta-dot.ta-busy{background:#818cf8;box-shadow:0 0 0 0 rgba(129,140,248,.6);animation:pulse 1.4s infinite}
 @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(129,140,248,.55)}70%{box-shadow:0 0 0 7px rgba(129,140,248,0)}100%{box-shadow:0 0 0 0 rgba(129,140,248,0)}}
 .ta-main{font-size:13px;font-weight:600;color:#e6e6e6;white-space:nowrap}
 .ta-sub{font-size:11px;color:#8a97a5;white-space:nowrap}
 .ta-split{position:relative;display:flex}
 .ta-btn{display:inline-flex;align-items:center;gap:6px;border:0;background:#6366f1;color:#fff;
   font-size:12px;font-weight:600;padding:7px 10px;cursor:pointer;border-radius:7px 0 0 7px}
 .ta-btn:hover{background:#818cf8}
 .ta-btn:disabled{opacity:.6;cursor:default}
 .ta-caret{border-radius:0 7px 7px 0;padding:7px 7px;border-left:1px solid rgba(255,255,255,.18)}
 .ta-spin{animation:spin 1s linear infinite}
 @keyframes spin{to{transform:rotate(360deg)}}
 .ta-menu{position:absolute;right:0;top:calc(100% + 6px);z-index:30;width:240px;padding:4px;
   background:#1a2440;border:1px solid #243054;border-radius:9px;box-shadow:0 12px 28px rgba(0,0,0,.45)}
 .ta-menu button{display:block;width:100%;text-align:left;background:none;border:0;color:inherit;
   padding:8px 10px;border-radius:6px;cursor:pointer}
 .ta-menu button:hover{background:#243054}
 .ta-menu .t{display:block;font-size:13px;color:#f8fafc}
 .ta-menu .d{display:block;font-size:11px;color:#8a97a5;margin-top:1px}
</style></head><body>
<header class="hdr">
 <h1>FPL Transfer Optimizer <span class="muted">— one tab per team · runs in parallel</span></h1>
 <div id="ta" class="ta">
   <span id="ta-dot" class="ta-dot"></span>
   <div class="ta-text">
     <div id="ta-main" class="ta-main">Transfer Algorithm data…</div>
     <div id="ta-sub" class="ta-sub"></div>
   </div>
   <div class="ta-split">
     <button id="ta-btn" class="ta-btn" onclick="taRefresh(false)" title="Check Patreon for an updated Transfer Algorithm post">
       <svg id="ta-ico" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
       <span id="ta-label">Sync</span>
     </button>
     <button id="ta-more" class="ta-btn ta-caret" aria-label="More sync options" onclick="taMenu(event)">
       <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m6 9 6 6 6-6"/></svg>
     </button>
     <div id="ta-menu" class="ta-menu hidden">
       <button onclick="taRefresh(true)">
         <span class="t">Force re-import</span>
         <span class="d">Re-download the latest post even if it looks unchanged</span>
       </button>
     </div>
   </div>
 </div>
</header>
<main>
 <div id="tabbar"></div>
 <div id="panels"></div>
</main>
<script>
// Each tab is an independent optimization: its own form, job, polling, results.
const DEFAULT_CSV = "__DEFAULT_CSV__";
let seq = 0;
const tabs = [];           // {id, name, state, el, panel, form, job, timer, startTs}
let activeId = null;

function glyphFor(state){
  return state==='running' ? '<span class="glyph" style="color:#f7b955">\u27f3</span>'
       : state==='done'    ? '<span class="glyph" style="color:#7ee787">\u2713</span>'
       : state==='error'   ? '<span class="glyph" style="color:#f77">\u26a0</span>'
       : '<span class="glyph muted">\u00b7</span>';
}

function renderTabBar(){
  const bar = document.getElementById('tabbar');
  bar.innerHTML = '';
  tabs.forEach(t => {
    const d = document.createElement('div');
    d.className = 'tab' + (t.id===activeId ? ' active' : '');
    d.innerHTML = glyphFor(t.state) + '<span>'+ (t.name||'New') +'</span>'
                + '<span class="close" title="close">\u00d7</span>';
    d.onclick = (e) => {
      if (e.target.classList.contains('close')) { closeTab(t.id); return; }
      activate(t.id);
    };
    bar.appendChild(d);
  });
  const add = document.createElement('div');
  add.id = 'addtab'; add.textContent = '+ add team';
  add.onclick = () => { newTab(); };
  bar.appendChild(add);
}

function activate(id){
  activeId = id;
  tabs.forEach(t => t.panel.classList.toggle('hidden', t.id!==id));
  renderTabBar();
}

function closeTab(id){
  const i = tabs.findIndex(t=>t.id===id);
  if (i<0) return;
  const t = tabs[i];
  if (t.timer) clearInterval(t.timer);
  t.panel.remove();
  tabs.splice(i,1);
  if (activeId===id) activeId = tabs.length ? tabs[Math.max(0,i-1)].id : null;
  if (!tabs.length) newTab();
  else activate(activeId);
}

function panelHTML(){
  return `
  <form onsubmit="return false;">
    <label>Manager ID
      <input name="manager_id" value="403851" required></label>
    <label>Max transfers to search
      <input name="transfers" type="number" min="1" max="5" value="2"></label>
    <label>Search method
      <select name="method">
        <option value="auto">Auto (exhaustive if \u22642 &amp; fits budget, else beam)</option>
        <option value="exhaustive">Exhaustive \u2014 guaranteed best (slow, depth \u22642)</option>
        <option value="beam">Beam \u2014 fast, near-exact (any depth)</option>
      </select></label>
    <label>Time budget (beam / deep searches)
      <select name="budget">
        <option value="60">1 minute</option>
        <option value="120">2 minutes</option>
        <option value="300" selected>5 minutes</option>
        <option value="600">10 minutes</option>
        <option value="1200">20 minutes</option>
        <option value="3600">60 minutes</option>
      </select></label>
    <label>Data source
      <select name="source">
        <option value="supabase" selected>Supabase (final_projections)</option>
        <option value="csv">CSV file</option>
      </select></label>
    <label>Horizon (weeks)
      <input name="horizon" type="number" min="1" max="8" value="8"></label>
    <label class="full">CSV path (used when source = CSV)
      <input name="csv_path" value="${DEFAULT_CSV}"></label>
    <label>Free transfers override (blank = auto from FPL)
      <input name="free" value=""></label>
    <label>Decay
      <input name="decay" value="0.85"></label>
    <button class="run" type="submit">Run optimization</button>
  </form>
  <div class="progress card hidden">
    <h3>Running\u2026 <span class="elapsed muted"></span></h3>
    <p class="muted">Deep searches can take a few minutes. Live progress below.
       You can switch tabs \u2014 this keeps running.</p>
    <pre class="plog log"></pre>
  </div>
  <div class="results"></div>`;
}

function newTab(){
  seq += 1;
  const id = seq;
  const panel = document.createElement('div');
  panel.innerHTML = panelHTML();
  document.getElementById('panels').appendChild(panel);
  const t = {id, name:'New', state:'idle', panel,
             form: panel.querySelector('form'),
             job:null, timer:null, startTs:0};
  tabs.push(t);
  wire(t);
  activate(id);
}

function wire(t){
  const btn = t.form.querySelector('button.run');
  const prog = t.panel.querySelector('.progress');
  const plog = t.panel.querySelector('.plog');
  const elapsed = t.panel.querySelector('.elapsed');
  const results = t.panel.querySelector('.results');

  t.form.addEventListener('submit', async () => {
    const params = new URLSearchParams(new FormData(t.form)).toString();
    t.name = t.form.manager_id.value || 'New';
    t.state = 'running'; renderTabBar();
    results.innerHTML = ''; plog.textContent = '';
    prog.classList.remove('hidden');
    btn.disabled = true; btn.textContent = 'Running\u2026';
    t.startTs = Date.now();
    let job;
    try { job = (await (await fetch('/run?'+params)).json()).job; }
    catch(e){ plog.textContent='Failed to start: '+e; btn.disabled=false; btn.textContent='Run optimization'; t.state='error'; renderTabBar(); return; }
    t.job = job;
    if (t.timer) clearInterval(t.timer);
    t.timer = setInterval(()=>poll(t), 1200);
    poll(t);
  });

  async function poll(t){
    const prog = t.panel.querySelector('.progress');
    const plog = t.panel.querySelector('.plog');
    const elapsed = t.panel.querySelector('.elapsed');
    const results = t.panel.querySelector('.results');
    const btn = t.form.querySelector('button.run');
    elapsed.textContent = '(' + Math.floor((Date.now()-t.startTs)/1000) + 's)';
    let s;
    try { s = await (await fetch('/status?job='+t.job)).json(); } catch(e){ return; }
    plog.textContent = (s.log||[]).join('\n');
    plog.scrollTop = plog.scrollHeight;
    if (s.state==='done' || s.state==='error'){
      clearInterval(t.timer); t.timer=null;
      prog.classList.add('hidden');
      results.innerHTML = s.html || '';
      btn.disabled=false; btn.textContent='Run optimization';
      t.state = s.state; renderTabBar();
    }
  }
}

newTab();   // start with one tab

// ---- Transfer Algorithm status chip (header) ----
function ago(iso){
  const s = Math.max(0, (Date.now() - new Date(iso).getTime())/1000);
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s/60) + ' min ago';
  if (s < 86400) return Math.round(s/3600) + ' h ago';
  if (s < 172800) return 'yesterday';
  return Math.round(s/86400) + ' days ago';
}
let taWatching = false, taNotice = '';
function taSetBusy(busy, label){
  const btn = document.getElementById('ta-btn'), more = document.getElementById('ta-more');
  btn.disabled = busy; more.disabled = busy;
  document.getElementById('ta-ico').classList.toggle('ta-spin', busy);
  document.getElementById('ta-label').textContent = label || (busy ? 'Syncing\u2026' : 'Sync');
}
async function taLoad(){
  let s;
  try { s = await (await fetch('/ta_status')).json(); } catch(e){ return null; }
  const main = document.getElementById('ta-main'), sub = document.getElementById('ta-sub');
  const dot = document.getElementById('ta-dot'), chip = document.getElementById('ta');
  const c = s.capture, r = s.run;
  const running = !!r && r.status !== 'completed';
  const failed = !!r && r.status === 'completed' && r.conclusion && r.conclusion !== 'success';

  if (c) {
    main.textContent = 'GW' + c.uploaded_for_gw + ' data \u00b7 updated ' + ago(c.captured_at);
    chip.title = 'Transfer Algorithm data captured ' + new Date(c.captured_at).toLocaleString()
               + ' \u2014 ' + c.row_count + ' rows, ' + c.player_count + ' players';
  } else {
    main.textContent = 'No Transfer Algorithm data';
  }

  if (taNotice) sub.textContent = taNotice;
  else if (running) sub.textContent = 'Import running\u2026';
  else if (r) sub.textContent = 'Last check ' + ago(r.createdAt) + (failed ? ' \u00b7 run failed' : '');
  else if (!s.gh) sub.textContent = 'Sync unavailable (GitHub CLI not found)';
  else sub.textContent = '';

  dot.className = 'ta-dot ' + (running || taWatching ? 'ta-busy' : failed ? 'ta-warn' : c ? 'ta-ok' : '');
  if (!s.gh) { taSetBusy(true, 'Sync'); return false; }
  if (!taWatching) taSetBusy(running);
  return running;
}
function taMenu(e){
  e.stopPropagation();
  document.getElementById('ta-menu').classList.toggle('hidden');
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('.ta-split')) document.getElementById('ta-menu').classList.add('hidden');
});
async function taRefresh(force){
  document.getElementById('ta-menu').classList.add('hidden');
  const before = (await (await fetch('/ta_status')).json()).capture;
  taNotice = ''; taWatching = true; taSetBusy(true);
  document.getElementById('ta-dot').className = 'ta-dot ta-busy';
  document.getElementById('ta-sub').textContent = 'Starting import\u2026';
  try {
    const j = await (await fetch('/ta_refresh?force=' + (force ? 1 : 0))).json();
    if (!j.ok) throw new Error(j.error || 'could not start');
  } catch(e) {
    taWatching = false; taNotice = 'Couldn\u2019t start sync'; await taLoad(); return;
  }
  document.getElementById('ta-sub').textContent = 'Checking Patreon\u2026';
  await new Promise(r => setTimeout(r, 6000));      // let GitHub register the run
  let s = null;
  for (let i = 0; i < 60; i++) {                    // follow it, up to ~3 min
    try { s = await (await fetch('/ta_status')).json(); } catch(e) {}
    if (s && s.run && s.run.status === 'completed') break;
    await new Promise(r => setTimeout(r, 3000));
  }
  taWatching = false;
  const after = s && s.capture;
  if (s && s.run && s.run.conclusion && s.run.conclusion !== 'success') taNotice = 'Sync failed';
  else if (after && (!before || after.captured_at !== before.captured_at)) taNotice = 'New data imported';
  else taNotice = 'Up to date \u2014 no newer post';
  await taLoad();
  setTimeout(() => { taNotice = ''; taLoad(); }, 12000);
}
taLoad();
setInterval(() => { if (!taWatching) taLoad(); }, 60000);
</script>
</body></html>"""


def form_page():
    return PAGE.replace("__DEFAULT_CSV__", DEFAULT_CSV)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, code=200):
        import json
        self._send(json.dumps(obj), code, "application/json")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send(form_page())
            return

        if path == "/ta_status":
            self._send_json(ta_status())
            return

        if path == "/ta_refresh":
            q = parse_qs(parsed.query)
            force = q.get("force", ["0"])[0] == "1"
            try:
                ta_refresh(force)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/status":
            q = parse_qs(parsed.query)
            job_id = q.get("job", [""])[0]
            job = JOBS.get(job_id)
            if not job:
                self._send_json({"state": "unknown"}, 404)
                return
            self._send_json({
                "state": job["state"],          # running | done | error
                "log": job["log"][-400:],       # recent progress lines
                "html": job.get("html", ""),    # result HTML when done
            })
            return

        if path == "/run":
            # Start a job and return its id immediately (non-blocking).
            q = parse_qs(parsed.query)
            def g(k, d=""):
                return q.get(k, [d])[0]
            params = {
                "manager_id": g("manager_id", "403851"),
                "source": g("source", "csv"),
                "transfers": g("transfers", "2"),
                "horizon": g("horizon", "8"),
                "decay": g("decay", "0.85"),
                "csv_path": g("csv_path", DEFAULT_CSV),
                "free_raw": g("free", "").strip(),
                "method": g("method", "auto"),
                "budget": g("budget", "300"),
            }
            job_id = _start_job(params)
            self._send_json({"job": job_id})
            return

        self._send(form_page(), 404)


GH_REPO = "SLC-Online/fpl-price-tracker"
GH_WORKFLOW = "scrape_patreon.yml"


def _find_gh():
    """Locate the GitHub CLI. A double-clicked .app has a minimal PATH, so
    check the usual install locations explicitly."""
    import shutil
    for p in (shutil.which("gh"), "/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
        if p and os.path.exists(p):
            return p
    return None


def _run_gh(args, timeout=30):
    import subprocess
    gh = _find_gh()
    if not gh:
        raise RuntimeError("GitHub CLI (gh) not found")
    r = subprocess.run([gh] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "gh failed").strip()[:300])
    return r.stdout


def ta_status():
    """Latest Transfer Algorithm capture in Supabase + latest import-workflow run."""
    import json as _json
    out = {"capture": None, "run": None, "gh": bool(_find_gh()), "error": None}
    try:
        url, key = O.load_env()
        h = {"apikey": key, "Authorization": "Bearer " + key}
        r = O.requests.get(
            url.rstrip("/") + "/rest/v1/projection_captures", headers=h, timeout=20,
            params={"select": "captured_at,uploaded_for_gw,row_count,player_count",
                    "source_id": "eq.1", "order": "captured_at.desc", "limit": "1"})
        rows = r.json()
        if rows:
            out["capture"] = rows[0]
    except Exception as e:
        out["error"] = f"Supabase: {e}"
    if out["gh"]:
        try:
            raw = _run_gh(["run", "list", "-R", GH_REPO, "--workflow", GH_WORKFLOW,
                           "--limit", "1", "--json", "status,conclusion,createdAt,event"])
            runs = _json.loads(raw)
            if runs:
                out["run"] = runs[0]
        except Exception as e:
            out["error"] = (out["error"] or "") + f" GitHub: {e}"
    return out


def ta_refresh(force=False):
    args = ["workflow", "run", GH_WORKFLOW, "-R", GH_REPO]
    if force:
        args += ["-f", "force_reimport=true"]
    _run_gh(args)


JOBS = {}
_JOB_SEQ = [0]


def _start_job(params):
    _JOB_SEQ[0] += 1
    job_id = str(_JOB_SEQ[0])
    job = {"state": "running", "log": [], "html": "", "params": params}
    JOBS[job_id] = job

    def log(s):
        job["log"].append(s)
        print(s, flush=True)

    def worker():
        try:
            res = run_optimization(
                manager_id=int(params["manager_id"]),
                csv_path=params["csv_path"],
                use_supabase=(params["source"] == "supabase"),
                transfers=int(params["transfers"]),
                horizon=int(params["horizon"]),
                decay=float(params["decay"]),
                free_override=(int(params["free_raw"]) if params["free_raw"] else None),
                log=log,
                budget_secs=float(params["budget"]),
                method_override=(None if params["method"] == "auto" else params["method"]),
            )
            job["html"] = render_result(res)
            job["state"] = "done"
        except Exception as e:
            tb = traceback.format_exc()
            job["html"] = (f'<div class="card"><p class="warn">Error: {esc(e)}</p>'
                           f'<pre class="log">{esc(tb)}</pre></div>')
            job["state"] = "error"
            log(f"ERROR: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"FPL optimizer UI running at {url}")
    print("Open that URL in your browser. Press Ctrl+C to stop.")
    # Auto-open the browser unless told not to (the .app launcher opens it).
    if os.environ.get("OPT_NO_BROWSER") != "1":
        try:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
