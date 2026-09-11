import os, time
import optimizer_app as O

url=os.environ["SUPABASE_URL"]; key=os.environ["SUPABASE_SERVICE_KEY"]
bs=O.get_bootstrap()
mgr,squad,pur,bank,ft,ng=O.get_manager_squad(403851,bs)
players,gws,latest=O.load_players(url,key,bs,ng,8)
for eid in squad:
    p=players[eid]; p.purchase_price=pur.get(eid,p.now_cost); p.selling_price=O.selling_price(p.purchase_price,p.now_cost)

def run(use_prune):
    t=time.time()
    base,ranked=O.optimize(squad,players,gws,0.85,bank,2,max_transfers=2,top_n=10,use_prune=use_prune)
    return base,ranked,time.time()-t

print("=== WITH prune ===")
b1,r1,t1=run(True)
print(f"time {t1:.1f}s")
print("=== WITHOUT prune (true exhaustive) ===")
b2,r2,t2=run(False)
print(f"time {t2:.1f}s")

def sig(m):
    ins=tuple(sorted(n.web_name for _,n in m.transfers))
    outs=tuple(sorted(o.web_name for o,_ in m.transfers))
    return (outs,ins,round(m.net,3))

print("\nTop 8 WITH prune:")
for m in r1[:8]: print("  ",round(m.net,2), "|", "; ".join(f"{o.web_name}->{n.web_name}" for o,n in m.transfers))
print("Top 8 WITHOUT prune:")
for m in r2[:8]: print("  ",round(m.net,2), "|", "; ".join(f"{o.web_name}->{n.web_name}" for o,n in m.transfers))

# compare best net
print(f"\nBest net WITH prune:    {r1[0].net:+.3f}")
print(f"Best net WITHOUT prune: {r2[0].net:+.3f}")
print("SAME best net?", abs(r1[0].net-r2[0].net)<1e-6)
print("SAME top plan?", sig(r1[0])==sig(r2[0]))
