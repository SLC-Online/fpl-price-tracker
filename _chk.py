import requests, os, json
U=os.environ["SUPABASE_URL"]; K=os.environ["SUPABASE_SERVICE_KEY"]
H={"apikey":K,"Authorization":f"Bearer {K}"}
def g(q):
    return requests.get(f"{U}/rest/v1/{q}",headers=H,timeout=20).json()
# latest snapshot
snap=g("snapshots?select=snapshot_id,timestamp&order=snapshot_id.desc&limit=1")
sid=snap[0]["snapshot_id"]
print("latest snapshot", sid, snap[0]["timestamp"])
# sample a few players' transfer fields
rows=g(f"player_snapshots?select=element_id,transfers_in_event,transfers_out_event,now_cost,selected_by_percent&snapshot_id=eq.{sid}&order=transfers_in_event.desc&limit=5")
print("Top transferred-in players this GW:")
for r in rows: print(" ",r)
# latest TA capture
cap=g("projection_captures?select=id,uploaded_for_gw,captured_at,player_count&source_id=eq.1&order=captured_at.desc&limit=3")
print("latest TA captures:", cap)
