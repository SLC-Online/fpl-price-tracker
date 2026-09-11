import os, itertools
import optimizer_app as O

# Reproduce the exact candidate pool + count how many squads get SCORED for a
# 2-transfer search, and time a single squad scoring, so we can see if 13s is real.
url=os.environ["SUPABASE_URL"]; key=os.environ["SUPABASE_SERVICE_KEY"]
bs=O.get_bootstrap()
mgr,squad,pur,bank,ft,ng=O.get_manager_squad(403851,bs)
players,gws,latest=O.load_players(url,key,bs,ng,8)
for eid in squad:
    p=players[eid]; p.purchase_price=pur.get(eid,p.now_cost); p.selling_price=O.selling_price(p.purchase_price,p.now_cost)

current=[players[i] for i in squad]
owned=set(squad)
bypos={1:[],2:[],3:[],4:[]}
for p in players.values():
    if p.element_id in owned or not p.projections: continue
    bypos[p.element_type].append(p)

# replicate dominance prune
def prune(cands):
    cands=sorted(cands,key=lambda p:(p.now_cost,-p.twxp(gws,0.85)))
    kept=[]
    for p in cands:
        pv=[p.projections.get(g,0.0) for g in gws]
        dom=False
        for q in kept:
            if q.now_cost<=p.now_cost and all(q.projections.get(g,0.0)>=pv[i] for i,g in enumerate(gws)):
                dom=True;break
        if not dom: kept.append(p)
    return kept
for pos in bypos: bypos[pos]=prune(bypos[pos])
print("pruned pool sizes:", {k:len(v) for k,v in bypos.items()})

# count candidate squads actually scored for depth 2
import time
sell={p.element_id:p.selling_price for p in current}
count=0
for out_combo in itertools.combinations(current,2):
    need=[p.element_type for p in out_combo]
    budget=bank+sum(sell[p.element_id] for p in out_combo)
    lists=[bypos[pos] for pos in need]
    for in_combo in itertools.product(*lists):
        if len({p.element_id for p in in_combo})!=2: continue
        if sum(p.now_cost for p in in_combo)>budget: continue
        count+=1
print("candidate squads passing budget (depth 2):", count)

# time scoring one squad
t=time.time()
for _ in range(1000):
    O.squad_twxp(current,gws,0.85)
per=(time.time()-t)/1000
print(f"squad_twxp time: {per*1e6:.1f} microseconds each")
print(f"=> est. score time for {count} squads: {count*per:.1f} s")
