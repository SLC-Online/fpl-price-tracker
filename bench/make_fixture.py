#!/usr/bin/env python3
"""Freeze one optimiser input (squad, prices, projections) to JSON, so the
Python and JavaScript engines can be run on byte-identical data.

    /usr/bin/python3 bench/make_fixture.py 403851 bench/fixture_403851.json
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import optimizer_app as O  # noqa: E402

mid, out = int(sys.argv[1]), sys.argv[2]
HORIZON, DECAY = 8, 0.85

url, key = O.load_env()
bs = O.get_bootstrap()
mgr, squad_ids, purchase, bank, ft, next_gw = O.get_manager_squad(mid, bs)
players, gws, latest_gw = O.load_players(url, key, bs, next_gw, HORIZON)
gws = gws[:HORIZON]

by_id = {e["id"]: e for e in bs["elements"]}
tshort = {t["id"]: t["short_name"] for t in bs["teams"]}
for eid in squad_ids:
    if eid not in players:
        e = by_id[eid]
        players[eid] = O.Player(eid, e["web_name"], e["element_type"], e["team"],
                                tshort.get(e["team"], "?"), e["now_cost"])
    p = players[eid]
    p.purchase_price = purchase.get(eid, p.now_cost)
    p.selling_price = O.selling_price(p.purchase_price, p.now_cost)

json.dump({
    "manager": mid, "team": mgr["team_name"], "upload_gw": latest_gw,
    "gws": gws, "decay": DECAY,
    # Weights computed by Python here, so both engines use identical values.
    "weights": [DECAY ** i for i in range(len(gws))],
    "bank": bank, "free_transfers": ft, "squad_ids": list(squad_ids),
    "players": [{
        "id": p.element_id, "name": p.web_name, "pos": p.element_type,
        "team": p.team_id, "team_short": p.team_short, "cost": p.now_cost,
        "sell": p.selling_price,
        "proj": [[g, v] for g, v in p.projections.items()],
    } for p in players.values()],     # dict order preserved: it affects tie order
}, open(out, "w"))
print(f"{mgr['team_name']}: {len(players)} players, GW{gws[0]}-{gws[-1]}, bank {bank}, FT {ft}")
