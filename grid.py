#!/usr/bin/env python3
"""Per-player weekly grid for a squad after given transfers.
Rows = players, columns = gameweeks. Shows each starter's projected points that
week, (C) for captain (points shown DOUBLED), 'bench' when not in the XI, and
the weekly XI total. Usage:
    python3 grid.py <manager_id> <csv> <horizon> [OUT IN ...]
"""
import sys
import optimize_from_csv as C
import optimizer_app as O

mid = int(sys.argv[1]); csv_path = sys.argv[2]; horizon = int(sys.argv[3])
pairs = sys.argv[4:]
transfers = list(zip(pairs[0::2], pairs[1::2]))

bs = O.get_bootstrap()
byid = {e['id']: e for e in bs['elements']}
tshort = {t['id']: t['short_name'] for t in bs['teams']}
mgr, squad, pur, bank, ft, ng = O.get_manager_squad(mid, bs)
players, all_gws, unm = C.load_csv_projections(csv_path, bs)
gws = all_gws[:horizon]

for eid in squad:
    if eid not in players:
        e = byid[eid]
        players[eid] = O.Player(eid, e['web_name'], e['element_type'], e['team'], tshort.get(e['team'], '?'), e['now_cost'])
    p = players[eid]; p.purchase_price = pur.get(eid, p.now_cost); p.selling_price = O.selling_price(p.purchase_price, p.now_cost)

def find(name):
    for eid in squad:
        if players[eid].web_name == name:
            return players[eid]
    for p in players.values():
        if p.web_name == name:
            return p
    raise SystemExit("not found: " + name)

ids = set(squad)
tlabel = []
for o, i in transfers:
    po, pi = find(o), find(i)
    ids.discard(po.element_id); ids.add(pi.element_id)
    tlabel.append(f"{po.web_name}->{pi.web_name}")
sq = [players[i] for i in ids]

# Determine per-GW XI + captain
POS = {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}
weekly = {}   # gw -> (starter_ids set, captain_id, total)
for gw in gws:
    total, formation, starters, cap, bench = O.best_xi_detail(sq, gw)
    weekly[gw] = ({p.element_id for p, _ in starters}, cap[0].element_id, total, formation)

hdr = f"{mgr['team_name']}"
if tlabel:
    hdr += "  |  transfers: " + ", ".join(tlabel)
else:
    hdr += "  |  NO transfers (current squad)"
print("\n" + hdr)
print(f"(projected points per player per GW; (C)=captain, doubled; '·'=benched that week)\n")

# column header
colw = 7
line = f"{'Player':18s}{'Pos':4s}{'Team':5s}"
for gw in gws:
    line += f"GW{gw:<5}"
print(line)
print("-" * len(line))

# sort players by position then by total contribution
def ptotal(p):
    return sum(p.projections.get(g, 0.0) for g in gws)
order = sorted(sq, key=lambda p: (p.element_type, -ptotal(p)))

for p in order:
    row = f"{p.web_name[:17]:18s}{POS[p.element_type]:4s}{p.team_short:5s}"
    for gw in gws:
        starters, capid, total, form = weekly[gw]
        v = p.projections.get(gw, 0.0)
        if p.element_id in starters:
            if p.element_id == capid:
                cell = f"{v*2:.2f}C"
            else:
                cell = f"{v:.2f}"
        else:
            cell = "·"
        row += f"{cell:<7}"
    print(row)

print("-" * len(line))
tot_row = f"{'XI TOTAL':18s}{'':4s}{'':5s}"
for gw in gws:
    tot_row += f"{weekly[gw][2]:<7.1f}"
print(tot_row)
form_row = f"{'Formation':18s}{'':4s}{'':5s}"
for gw in gws:
    d, m, f = weekly[gw][3]
    form_row += f"{f'{d}-{m}-{f}':<7}"
print(form_row)
grand = sum(weekly[gw][2] for gw in gws)
dec = O.squad_twxp(sq, gws, 0.85)
print(f"\nRaw 8wk sum: {grand:.1f}   |   Decayed (0.85) TWxP: {dec:.2f}")
