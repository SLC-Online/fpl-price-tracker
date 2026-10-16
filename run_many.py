#!/usr/bin/env python3
"""
Run the brute-force transfer optimizer on SEVERAL managers in parallel.

This is the self-serve tool: you run it yourself, no need to ask the agent.
It fans out one worker process per manager (so they run in tandem on multiple
CPU cores), each pulling projections from Supabase, and prints a combined
report with best options at every depth + this-week XI/captain.

USAGE
    cd /Users/edbeesley/world-cup-fantasy/fpl_project
    /usr/bin/python3 run_many.py 403851 9919536 8886030
    /usr/bin/python3 run_many.py 403851 9919536 --transfers 2
    /usr/bin/python3 run_many.py 9919536 --transfers 5            # deep: auto beam search
    /usr/bin/python3 run_many.py 9919536 --transfers 5 --beam --beam-width 800
    /usr/bin/python3 run_many.py 403851 --transfers 3 --full      # exact, slow

FLAGS
    --transfers N   max transfer depth to search (default 2)
    --full          force full exhaustive: guaranteed optimum, slow
    --fast          dominance-prune exhaustive: faster, ~exact
    --beam          staged beam search (default for depth >= 3; makes depth 4-5 feasible)
    --beam-width K  beam: squads kept per stage (default 400; higher = more exact)
    --pool N        beam: top-N buy candidates per position (default 25)
    --horizon N     gameweeks to plan over (default 8)
    --decay D       weekly decay (default 0.85)

Method auto-select: depth <= 2 -> full exhaustive (exact); depth >= 3 -> beam.
The beam was validated to match full exhaustive exactly at depth 2.
Each run also prints a MARGINAL GAIN curve so you can see where extra
transfers stop being worth it (crucial when you have many free transfers).

Results also written to /tmp/opt_<managerid>.txt for each team.
"""
import sys, os, time, argparse, multiprocessing as mp
import optimizer_app as O


def analyze(mid, transfers, method, horizon, decay, beam_width, pool_per_pos):
    """Run one manager; return a formatted text block.

    method: 'full' (exhaustive, exact), 'fast' (dominance prune), or
            'beam' (staged beam search, for deep searches)."""
    t0 = time.time()
    url, key = O.load_env()
    bs = O.get_bootstrap()
    mgr, squad_ids, purchase, bank, ft, next_gw = O.get_manager_squad(mid, bs)
    players, gws, latest_gw = O.load_players(url, key, bs, next_gw, horizon)
    gws = gws[:horizon]

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

    base = O.squad_twxp([players[i] for i in squad_ids], gws, decay)
    missing = [players[eid].web_name for eid in squad_ids if not players[eid].projections]

    if method == 'beam':
        _, ranked = O.beam_optimize(squad_ids, players, gws, decay, bank, ft,
                                    max_transfers=transfers, beam_width=beam_width,
                                    pool_per_pos=pool_per_pos, top_n=60)
    else:
        _, ranked = O.optimize(squad_ids, players, gws, decay, bank, ft,
                               max_transfers=transfers, top_n=60,
                               use_prune=(method == 'fast'))
    elapsed = time.time() - t0

    by_depth = {}
    for m in ranked:
        by_depth.setdefault(len(m.transfers), []).append(m)

    mode = {'full': 'FULL exhaustive', 'fast': 'FAST/prune',
            'beam': f'BEAM (width {beam_width}, pool {pool_per_pos})'}[method]
    out = []
    out.append("\n" + "=" * 78)
    out.append(f"MANAGER {mid}  ·  {mgr['team_name']}  ·  {mgr['name']}   [{mode}, {elapsed:.0f}s]")
    out.append(f"Bank £{bank/10:.1f}m   FREE TRANSFERS: {ft}   Planning GW{next_gw}")
    out.append(f"Source: Supabase final_projections (upload GW{latest_gw})")
    out.append(f"Horizon GW{gws[0]}-GW{gws[-1]} ({len(gws)}wk, decay {decay})   Base xP {base:.2f}")
    if missing:
        out.append(f"⚠ No projection (scored 0): {', '.join(missing)}")

    this_gw = gws[0]

    def xi_block(m):
        sq = [players[i] for i in m.squad_ids]
        total, formation, starters, cap, bench = O.best_xi_detail(sq, this_gw)
        d, mm, f = formation
        lines = [f"    GW{this_gw} XI [{d}-{mm}-{f}]  total {total:.2f}  "
                 f"captain {cap[0].web_name} ({cap[1]:.2f}->{cap[1]*2:.2f})"]
        names = []
        for p, v in starters:
            tag = "(C)" if p.element_id == cap[0].element_id else ""
            names.append(f"{p.web_name}{tag} {v:.2f}")
        lines.append("      XI:    " + ", ".join(names))
        lines.append("      Bench: " + ", ".join(f"{p.web_name} {v:.2f}" for p, v in bench))
        return "\n".join(lines)

    for depth in range(1, transfers + 1):
        plans = by_depth.get(depth, [])[:6]
        hit = 0 if depth <= ft else (depth - ft) * O.HIT_COST
        hnote = "" if depth <= ft else f"  (uses {depth-ft} beyond {ft} free -> -{hit} hit)"
        out.append("\n" + "-" * 78)
        out.append(f"BEST {depth}-TRANSFER OPTIONS{hnote}")
        out.append("-" * 78)
        if not plans:
            out.append("  (none found)")
            continue
        for i, m in enumerate(plans):
            mv = "  +  ".join(f"{o.web_name}->{n.web_name}" for o, n in m.transfers)
            out.append(f"  #{i}  rawD {m.delta:+.2f}  hit -{m.hit}  net {m.net:+.2f}   {mv}")
        out.append(f"\n  Detail for best {depth}-transfer plan (this week):")
        out.append(xi_block(plans[0]))

    # Marginal-gain curve: how much does each additional transfer add? This is
    # the key readout when you have many free transfers — it shows where the
    # gains flatten so you can stop before a move stops being worth it.
    out.append("\n" + "-" * 78)
    out.append("MARGINAL GAIN PER TRANSFER (best plan at each depth)")
    out.append("-" * 78)
    out.append("  depth | best net | marginal | note")
    prev_net = 0.0
    for depth in range(1, transfers + 1):
        plans = by_depth.get(depth, [])
        if not plans:
            continue
        best = plans[0]
        marginal = best.net - prev_net
        note = ""
        if depth > ft:
            note = f"(incl -{O.HIT_COST} hit for this move)"
        flag = "  <= gain flattening" if (depth > 1 and marginal < 1.0) else ""
        out.append(f"   {depth:>4}  | {best.net:>+7.2f}  | {marginal:>+7.2f}  {note}{flag}")
        prev_net = best.net
    out.append("  (marginal = extra net points vs the best plan one transfer shallower.")
    out.append("   A small or negative marginal means that extra transfer isn't earning its place.)")

    text = "\n".join(out)
    with open(f"/tmp/opt_{mid}.txt", "w") as f:
        f.write(text + "\n")
    return text


def _worker(args):
    mid, transfers, method, horizon, decay, beam_width, pool_per_pos = args
    try:
        return mid, analyze(mid, transfers, method, horizon, decay, beam_width, pool_per_pos)
    except Exception as e:
        import traceback
        return mid, f"\n{'='*78}\nMANAGER {mid}  ·  ERROR: {e}\n{traceback.format_exc()}\n{'='*78}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("managers", nargs="+", type=int)
    ap.add_argument("--transfers", type=int, default=2)
    ap.add_argument("--fast", action="store_true", help="dominance prune (approx, fast)")
    ap.add_argument("--full", action="store_true", help="full exhaustive (exact, slow)")
    ap.add_argument("--beam", action="store_true",
                    help="staged beam search for deep plans (default for depth >= 3)")
    ap.add_argument("--beam-width", type=int, default=400,
                    help="beam search: squads kept per stage (higher = more exact, slower)")
    ap.add_argument("--pool", type=int, default=25,
                    help="beam search: top-N candidate buy targets per position")
    ap.add_argument("--horizon", type=int, default=8)
    ap.add_argument("--decay", type=float, default=0.85)
    a = ap.parse_args()

    # Method selection:
    #   explicit --full/--fast/--beam win; otherwise depth<=2 -> full exhaustive
    #   (exact, feasible), depth>=3 -> beam (deep search made tractable).
    if a.full:
        method = 'full'
    elif a.fast:
        method = 'fast'
    elif a.beam:
        method = 'beam'
    else:
        method = 'full' if a.transfers <= 2 else 'beam'

    mode = {'full': 'FULL exhaustive (exact)', 'fast': 'FAST/prune (approx)',
            'beam': f'BEAM width {a.beam_width} pool {a.pool} (deep, ~exact)'}[method]
    print(f"Running {len(a.managers)} manager(s) in parallel · depth {a.transfers} · {mode}")
    print("Managers:", ", ".join(map(str, a.managers)))
    print("(progress is quiet; each team prints when it finishes)\n", flush=True)

    jobs = [(m, a.transfers, method, a.horizon, a.decay, a.beam_width, a.pool)
            for m in a.managers]
    with mp.Pool(processes=min(len(jobs), os.cpu_count() or 2)) as pool:
        for mid, text in pool.imap_unordered(_worker, jobs):
            print(text, flush=True)

    print(f"\nAll done. Per-team results also saved to /tmp/opt_<id>.txt")


if __name__ == "__main__":
    main()
