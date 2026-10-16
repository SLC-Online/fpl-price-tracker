#!/usr/bin/env python3
"""Print the full per-GW breakdown (optimal XI, captain, formation) for a
manager's squad AFTER applying a specified set of transfers, using CSV projections.

Usage:
    python3 breakdown.py <manager_id> <csv> <horizon> OUT1 IN1 [OUT2 IN2 ...]
Players given by web_name (as shown in the optimizer output).
"""
import sys
import optimize_from_csv as C
import optimizer_app as O

mid = int(sys.argv[1]); csv_path = sys.argv[2]; horizon = int(sys.argv[3])
pairs = sys.argv[4:]
transfer_names = list(zip(pairs[0::2], pairs[1::2]))

bs = O.get_bootstrap()
byid = {e['id']: e for e in bs['elements']}
tshort = {t['id']: t['short_name'] for t in bs['teams']}
mgr, squad, pur, bank, ft, ng = O.get_manager_squad(mid, bs)
players, all_gws, unm = C.load_csv_projections(csv_path, bs)
gws = all_gws[:horizon]

# ensure squad players exist + prices
for eid in squad:
    if eid not in players:
        e = byid[eid]
        players[eid] = O.Player(eid, e['web_name'], e['element_type'], e['team'], tshort.get(e['team'], '?'), e['now_cost'])
    p = players[eid]; p.purchase_price = pur.get(eid, p.now_cost); p.selling_price = O.selling_price(p.purchase_price, p.now_cost)

def find(name):
    # among current squad first, then whole universe
    for eid in squad:
        if players[eid].web_name == name:
            return players[eid]
    for p in players.values():
        if p.web_name == name:
            return p
    raise SystemExit(f"Player not found: {name}")

new_squad_ids = set(squad)
applied = []
for out_n, in_n in transfer_names:
    o = find(out_n); i = find(in_n)
    new_squad_ids.discard(o.element_id); new_squad_ids.add(i.element_id)
    applied.append((o, i))

sq = [players[i] for i in new_squad_ids]

print(f"\n{mgr['team_name']} — squad after {len(applied)} transfer(s)")
for o, i in applied:
    print(f"   OUT {o.web_name} ({o.team_short}) £{o.selling_price/10:.1f}m  ->  IN {i.web_name} ({i.team_short}) £{i.now_cost/10:.1f}m")

base = O.squad_twxp([players[i] for i in squad], gws, 0.85)
new = O.squad_twxp(sq, gws, 0.85)
print(f"\n   {len(gws)}-week TWxP: {base:.2f} -> {new:.2f}  (Δ {new-base:+.2f})")

for gw in gws:
    total, formation, starters, cap, bench = O.best_xi_detail(sq, gw)
    d, m, f = formation
    xi = ", ".join(f"{p.web_name}{'(C)' if p.element_id==cap[0].element_id else ''} {v:.2f}" for p, v in starters)
    bn = ", ".join(f"{p.web_name} {v:.2f}" for p, v in bench)
    print(f"\n   GW{gw}  [{d}-{m}-{f}]  {total:.2f} pts  (capt {cap[0].web_name} {cap[1]:.2f}→{cap[1]*2:.2f})")
    print(f"     XI:    {xi}")
    print(f"     Bench: {bn}")
