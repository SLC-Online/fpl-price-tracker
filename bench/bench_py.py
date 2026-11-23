#!/usr/bin/env python3
"""Run the real Python engine (optimizer_app.optimize) on a frozen fixture.

    /usr/bin/python3 bench/bench_py.py bench/fixture_403851.json bench/result_py.json
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import optimizer_app as O  # noqa: E402

fx = json.load(open(sys.argv[1]))
players = {}
for p in fx["players"]:
    pl = O.Player(p["id"], p["name"], p["pos"], p["team"], p["team_short"], p["cost"])
    pl.projections = {g: v for g, v in p["proj"]}
    pl.selling_price = p["sell"]
    players[p["id"]] = pl

t0 = time.perf_counter()
base, ranked = O.optimize(fx["squad_ids"], players, fx["gws"], fx["decay"], fx["bank"],
                          fx["free_transfers"], max_transfers=2, top_n=20, use_prune=False)
secs = time.perf_counter() - t0

json.dump({
    "engine": "python", "seconds": secs, "base": base,
    "ranked": [{"depth": len(m.transfers),
                "moves": [[o.element_id, n.element_id] for o, n in m.transfers],
                "delta": m.delta, "net": m.net, "twxp": m.twxp} for m in ranked],
}, open(sys.argv[2], "w"))
print(f"python: {secs:.1f}s, base {base!r}")
