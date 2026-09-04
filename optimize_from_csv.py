#!/usr/bin/env python3
"""
Standalone optimizer that reads expected points DIRECTLY from a Transfer
Algorithm CSV (bypassing Supabase / the app). Name-matches CSV players to FPL
element IDs, pulls the target manager's squad + free transfers from the FPL
API, then brute-forces the best transfer plans over the CSV's gameweek horizon
with 0.85 decay and optimal-XI-per-GW rotation.

Usage:
    python3 optimize_from_csv.py <manager_id> <csv_path> [--transfers N] [--horizon N] [--free N]
"""
import sys, csv, re, argparse
from io import StringIO
from unicodedata import normalize, category

import optimizer_app as O   # reuse Player, engine, FPL API helpers

# CSV team code -> FPL short_name
TEAM_MAP = {
    'ARS': 'ARS', 'AVL': 'AVL', 'BOU': 'BOU', 'BRE': 'BRE', 'BRI': 'BHA',
    'CHE': 'CHE', 'COV': 'COV', 'CPL': 'CRY', 'CRY': 'CRY', 'EVE': 'EVE',
    'FUL': 'FUL', 'HUL': 'HUL', 'IPS': 'IPS', 'LEE': 'LEE', 'LIV': 'LIV',
    'MCI': 'MCI', 'MUN': 'MUN', 'NEW': 'NEW', 'NOT': 'NFO', 'NFO': 'NFO',
    'SUN': 'SUN', 'TOT': 'TOT', 'WHU': 'WHU', 'WOL': 'WOL', 'BUR': 'BUR',
}


def strip_accents(s):
    return ''.join(c for c in normalize('NFKD', s) if category(c) != 'Mn').lower().strip()


def parse_num(raw):
    """Parse a CSV numeric cell; '(x)' = negative; '-' / blank = None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == '-':
        return None
    neg = s.startswith('(') and s.endswith(')')
    s = s.strip('()').replace('%', '').replace(',', '').strip()
    if not s or s == '-':
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def match_player(csv_name, csv_team_short, bootstrap):
    """Fuzzy-match a CSV (name, team) to an FPL element_id."""
    from difflib import SequenceMatcher
    a = strip_accents(csv_name).replace('(', '').replace(')', '')
    best_id, best_score = None, 0.0
    for e in bootstrap['elements']:
        team_short = next((t['short_name'] for t in bootstrap['teams'] if t['id'] == e['team']), '')
        if team_short != csv_team_short:
            continue
        candidates = [
            e['web_name'],
            f"{e['first_name']} {e['second_name']}",
            e['second_name'],
        ]
        for cand in candidates:
            b = strip_accents(cand)
            if a == b:
                return e['id'], 1.0
            if a and b and (a in b or b in a):
                score = 0.92
            elif a.split() and b.split() and a.split()[-1] == b.split()[-1]:
                score = 0.86
            else:
                score = SequenceMatcher(None, a, b).ratio()
            if score > best_score:
                best_score, best_id = score, e['id']
    return (best_id, best_score) if best_score >= 0.75 else (None, best_score)


def load_csv_projections(csv_path, bootstrap):
    """Return (players_dict, gws, unmatched) with projections from the CSV.

    players_dict: element_id -> O.Player (only players present in the CSV that
    matched an FPL id). GW columns are read from the header.
    """
    with open(csv_path, encoding='latin-1') as f:
        text = f.read()
    reader = csv.reader(StringIO(text))
    header = next(reader)
    # locate GW columns: header cells that are pure integers
    gw_cols = []   # list of (col_index, gw_number)
    for i, h in enumerate(header):
        hs = h.strip()
        if hs.isdigit():
            gw_cols.append((i, int(hs)))
    gws = [g for _, g in gw_cols]

    team_short = {t['id']: t['short_name'] for t in bootstrap['teams']}
    by_id = {e['id']: e for e in bootstrap['elements']}

    players = {}
    unmatched = []
    for row in reader:
        if len(row) < 10:
            continue
        name = row[3].strip()
        team_raw = row[4].strip()
        if not name or name == '0' or not team_raw:
            continue
        team_api = TEAM_MAP.get(team_raw, team_raw)
        eid, score = match_player(name, team_api, bootstrap)
        if not eid:
            unmatched.append(f"{name} ({team_raw})")
            continue
        e = by_id.get(eid)
        if not e:
            continue
        p = O.Player(
            element_id=eid, web_name=e['web_name'], element_type=e['element_type'],
            team_id=e['team'], team_short=team_short.get(e['team'], '?'),
            now_cost=e['now_cost'],
        )
        for col_i, gw in gw_cols:
            if col_i < len(row):
                val = parse_num(row[col_i])
                if val is not None:
                    p.projections[gw] = val
        # only keep if it has at least one projection
        if p.projections:
            players[eid] = p
    return players, gws, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manager_id", type=int)
    ap.add_argument("csv_path")
    ap.add_argument("--transfers", type=int, default=3,
                    help="Max transfers to consider. Always searches hit-taking depths too; "
                         "a move beyond your free transfers only wins if it beats the free "
                         "options after the -4 hit.")
    ap.add_argument("--horizon", type=int, default=8)
    ap.add_argument("--decay", type=float, default=0.85)
    ap.add_argument("--free", type=int, default=None)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    print("Fetching FPL data…")
    bs = O.get_bootstrap()
    mgr, squad_ids, purchase, bank, ft, next_gw = O.get_manager_squad(args.manager_id, bs)

    print("Parsing CSV projections + name-matching…")
    players, all_gws, unmatched = load_csv_projections(args.csv_path, bs)

    # horizon: the first N gameweeks in the CSV (already start at next GW)
    gws = all_gws[:args.horizon]

    # attach squad prices; ensure squad players exist in the universe even if
    # they somehow weren't matched (build from bootstrap so optimization is valid)
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

    free = args.free if args.free is not None else ft

    print(f"\n{'='*70}")
    print(f"{mgr['team_name']}  ·  {mgr['name']}")
    print(f"Bank £{bank/10:.1f}m   Free transfers: {free}   Planning GW{next_gw}")
    print(f"CSV horizon: GW{gws[0]}–GW{gws[-1]}  ({len(gws)} weeks, decay {args.decay})")
    print(f"Matched {len(players)} CSV players; {len(unmatched)} unmatched")
    print('='*70)

    # squad coverage check
    missing = [eid for eid in squad_ids if not players[eid].projections]
    if missing:
        print("\n⚠ Squad players with NO CSV projection (scored 0 — check name match):")
        for eid in missing:
            print(f"   {players[eid].web_name} ({players[eid].team_short})")

    base = O.squad_twxp([players[i] for i in squad_ids], gws, args.decay)
    print(f"\nCurrent squad time-weighted xP over {len(gws)} weeks: {base:.2f}\n")

    print("Brute-forcing transfer plans…")
    base, ranked = O.optimize(squad_ids, players, gws, args.decay, bank, free,
                              max_transfers=args.transfers, top_n=args.top,
                              progress_cb=lambda s: print("  " + s, flush=True))

    # Group plans by number of transfers and show the best options at EACH depth,
    # so you can judge whether an extra transfer is worth it yourself.
    by_depth = {}
    for m in ranked:
        by_depth.setdefault(len(m.transfers), []).append(m)

    PER_DEPTH = 8
    for depth in sorted(d for d in by_depth if d > 0):
        plans = by_depth[depth][:PER_DEPTH]
        label = f"{depth} TRANSFER" + ("" if depth == 1 else "S")
        hitnote = "" if depth <= free else f"  (uses {depth - free} beyond your {free} free → -{(depth-free)*O.HIT_COST} hit)"
        print(f"\n{'='*70}")
        print(f"BEST {label}{hitnote}")
        print('='*70)
        print(f"{'#':>2}  {'Δ8wk':>7}  {'HIT':>4}  {'NET':>7}  MOVE")
        print('-'*70)
        for i, m in enumerate(plans):
            mv = "  +  ".join(f"{o.web_name}→{n.web_name}" for o, n in m.transfers)
            print(f"{i:>2}  {m.delta:>+7.2f}  {('-'+str(m.hit)) if m.hit else '0':>4}  {m.net:>+7.2f}  {mv}")

    # Per-depth best summary so the trade-off is obvious at a glance
    print(f"\n{'='*70}")
    print("TRADE-OFF SUMMARY (best plan at each depth)")
    print('='*70)
    print(f"  Keep squad (0 transfers):  0.00 gain")
    for depth in sorted(d for d in by_depth if d > 0):
        best_d = by_depth[depth][0]
        mv = "  +  ".join(f"{o.web_name}→{n.web_name}" for o, n in best_d.transfers)
        print(f"  {depth} transfer{'s' if depth>1 else ' '}: raw +{best_d.delta:.2f}  |  net +{best_d.net:.2f}  |  {mv}")
    print("\n  (Raw = expected-points gain over the horizon; Net = after any -4 hits.")
    print("   You decide whether each extra transfer's raw gain justifies using it.)")

    # Detailed per-GW breakdown for the best 1-transfer and best multi options
    def show_detail(m, header):
        print(f"\n{'='*70}\n{header}")
        print('='*70)
        for o, n in m.transfers:
            print(f"  OUT  {o.web_name:16s} £{o.selling_price/10:>4.1f}m   ->   "
                  f"IN  {n.web_name:16s} £{n.now_cost/10:>4.1f}m")
        print(f"\n  Raw Δ over {len(gws)} weeks: {m.delta:+.2f}   ·   Hit: -{m.hit}   ·   Net: {m.net:+.2f}")
        sq = [players[i] for i in m.squad_ids]
        print("\n  Optimal XI, captain & formation each gameweek:")
        for gw in gws:
            total, formation, starters, cap, bench = O.best_xi_detail(sq, gw)
            d, mm, f = formation
            names = []
            for p, v in starters:
                tag = "(C)" if p.element_id == cap[0].element_id else ""
                names.append(f"{p.web_name}{tag} {v:.1f}")
            print(f"\n   GW{gw}  [{d}-{mm}-{f}]  total {total:.1f}  (capt {cap[0].web_name} {cap[1]:.1f}→{cap[1]*2:.1f})")
            print("     XI:  " + ", ".join(names))
            print("     Bench: " + ", ".join(f"{p.web_name} {v:.1f}" for p, v in bench))

    if 1 in by_depth:
        show_detail(by_depth[1][0], "BEST SINGLE TRANSFER — full plan")
    return


if __name__ == "__main__":
    main()
