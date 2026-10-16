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


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>FPL Transfer Optimizer</title>
<style>
 body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;
   margin:0;background:#0f1419;color:#e6e6e6}}
 header{{background:#16202a;padding:16px 24px;border-bottom:1px solid #26313d}}
 h1{{margin:0;font-size:18px}}
 main{{max-width:1200px;margin:0 auto;padding:24px}}
 form{{background:#16202a;padding:20px;border-radius:10px;margin-bottom:24px;
   display:grid;grid-template-columns:repeat(2,1fr);gap:14px}}
 label{{display:flex;flex-direction:column;font-size:12px;color:#9fb0c0;gap:4px}}
 input,select{{padding:8px;border-radius:6px;border:1px solid #2c3a49;
   background:#0f1419;color:#e6e6e6;font-size:14px}}
 .full{{grid-column:1/3}}
 button{{grid-column:1/3;padding:12px;border:0;border-radius:8px;
   background:#2f81f7;color:#fff;font-size:15px;font-weight:600;cursor:pointer}}
 button:hover{{background:#1f6feb}}
 .card{{background:#16202a;border:1px solid #26313d;border-radius:10px;
   padding:18px;margin-bottom:20px;overflow-x:auto}}
 h2,h3,h4{{margin:0 0 10px}}
 .muted{{color:#8a97a5;font-weight:400}}
 .meta{{color:#b8c4d0}}
 .warn{{color:#f7b955;background:#2a2313;padding:8px 12px;border-radius:6px}}
 .ok{{color:#7ee787;background:#13251a;padding:8px 12px;border-radius:6px}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #26313d;
   vertical-align:top;white-space:nowrap}}
 th{{color:#9fb0c0;font-weight:600}}
 table.grid td.xi{{white-space:normal}}
 .pl{{display:inline-block;padding:2px 7px;margin:2px;border-radius:5px;
   background:#1d2a36}}
 .pl.cap{{background:#2f4a2f;color:#c9f5c9}}
 .gw{{font-weight:700}}
 .tot b{{color:#7ee787}}
 pre.log{{background:#0b0f14;color:#9fb0c0;padding:12px;border-radius:8px;
   white-space:pre-wrap;font-size:12px;max-height:260px;overflow:auto}}
</style></head><body>
<header><h1>FPL Transfer Optimizer <span class="muted">— brute-force, captaincy + rotation, 0.85 decay</span></h1></header>
<main>
<form method="get" action="/run">
 <label>Manager ID
   <input name="manager_id" value="{manager_id}" required></label>
 <label>Max transfers to search
   <input name="transfers" type="number" min="1" max="5" value="{transfers}"></label>
 <label>Search method
   <select name="method">
     <option value="auto" {auto_sel}>Auto (exhaustive if ≤2 & fits budget, else beam)</option>
     <option value="exhaustive" {exh_sel}>Exhaustive — guaranteed best (slow, depth ≤2)</option>
     <option value="beam" {beam_sel}>Beam — fast, near-exact (any depth)</option>
   </select></label>
 <label>Time budget (for beam / deep searches)
   <select name="budget">
     <option value="60" {b1_sel}>1 minute</option>
     <option value="120" {b2_sel}>2 minutes</option>
     <option value="300" {b5_sel}>5 minutes</option>
     <option value="600" {b10_sel}>10 minutes</option>
     <option value="1200" {b20_sel}>20 minutes</option>
     <option value="3600" {b60_sel}>60 minutes</option>
   </select></label>
 <label>Data source
   <select name="source">
     <option value="csv" {csv_sel}>CSV file</option>
     <option value="supabase" {sup_sel}>Supabase (final_projections)</option>
   </select></label>
 <label>Horizon (weeks)
   <input name="horizon" type="number" min="1" max="8" value="{horizon}"></label>
 <label class="full">CSV path (used when source = CSV)
   <input name="csv_path" value="{csv_path}"></label>
 <label>Free transfers override (blank = auto from FPL)
   <input name="free" value="{free}"></label>
 <label>Decay
   <input name="decay" value="{decay}"></label>
 <button type="submit">Run optimization</button>
</form>
{body}
</main></body></html>"""


def form_page(body="", manager_id="403851", transfers="2", horizon="8",
              decay="0.85", csv_path=DEFAULT_CSV, free="", source="csv",
              method="auto", budget="300"):
    return PAGE.format(
        manager_id=esc(manager_id), transfers=esc(transfers), horizon=esc(horizon),
        decay=esc(decay), csv_path=esc(csv_path), free=esc(free),
        csv_sel="selected" if source == "csv" else "",
        sup_sel="selected" if source == "supabase" else "",
        auto_sel="selected" if method == "auto" else "",
        exh_sel="selected" if method == "exhaustive" else "",
        beam_sel="selected" if method == "beam" else "",
        b1_sel="selected" if budget == "60" else "",
        b2_sel="selected" if budget == "120" else "",
        b5_sel="selected" if budget == "300" else "",
        b10_sel="selected" if budget == "600" else "",
        b20_sel="selected" if budget == "1200" else "",
        b60_sel="selected" if budget == "3600" else "",
        body=body,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(form_page())
            return
        if parsed.path != "/run":
            self._send(form_page(), 404)
            return

        q = parse_qs(parsed.query)
        def g(k, d=""):
            return q.get(k, [d])[0]

        manager_id = g("manager_id", "403851")
        source = g("source", "csv")
        transfers = g("transfers", "2")
        horizon = g("horizon", "8")
        decay = g("decay", "0.85")
        csv_path = g("csv_path", DEFAULT_CSV)
        free_raw = g("free", "").strip()
        method = g("method", "auto")
        budget = g("budget", "300")

        logs = []
        def log(s):
            logs.append(s)
            print(s, flush=True)

        try:
            res = run_optimization(
                manager_id=int(manager_id),
                csv_path=csv_path,
                use_supabase=(source == "supabase"),
                transfers=int(transfers),
                horizon=int(horizon),
                decay=float(decay),
                free_override=(int(free_raw) if free_raw else None),
                log=log,
                budget_secs=float(budget),
                method_override=(None if method == "auto" else method),
            )
            body = render_result(res)
            body += f'<div class="card"><h3>Run log</h3><pre class="log">{esc(chr(10).join(logs))}</pre></div>'
        except Exception as e:
            tb = traceback.format_exc()
            body = (f'<div class="card"><p class="warn">Error: {esc(e)}</p>'
                    f'<pre class="log">{esc(tb)}</pre>'
                    f'<pre class="log">{esc(chr(10).join(logs))}</pre></div>')

        self._send(form_page(
            body=body, manager_id=manager_id, transfers=transfers,
            horizon=horizon, decay=decay, csv_path=csv_path,
            free=free_raw, source=source, method=method, budget=budget,
        ))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"FPL optimizer UI running at {url}")
    print("Open that URL in your browser. Press Ctrl+C to stop.")
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
