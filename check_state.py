#!/usr/bin/env python3
import requests, os, json
U=os.environ["SUPABASE_URL"]; K=os.environ["SUPABASE_SERVICE_KEY"]
H={"apikey":K,"Authorization":f"Bearer {K}"}
def g(q):
    r=requests.get(f"{U}/rest/v1/{q}",headers=H,timeout=20); return r.status_code, r.json()

# ALL transfer_algorithm (source 1) captures, newest first
sc,caps=g("projection_captures?select=id,uploaded_for_gw,captured_at,row_count,player_count,meta&source_id=eq.1&order=captured_at.desc&limit=20")
print("SOURCE-1 (transfer_algorithm) CAPTURES newest first:")
for c in caps if isinstance(caps,list) else []:
    print(f"  id={c['id']} gw={c['uploaded_for_gw']} rows={c['row_count']} players={c['player_count']} at={c['captured_at'][:19]} meta={c['meta']}")

# Haaland (411) rows across ALL source-1 captures for gw3
sc,pi=g("projection_inputs?select=capture_id,gameweek,expected_points,source_id&element_id=eq.411&gameweek=eq.3&source_id=eq.1&order=capture_id.desc")
print("\nHaaland gw3 rows in source-1 projection_inputs:", json.dumps(pi)[:600])

# what final_projections returns for Haaland gw3 (the app's actual value)
sc,fp=g("final_projections?select=gameweek,expected_points,uploaded_for_gw&element_id=eq.411&gameweek=eq.3")
print("\nfinal_projections Haaland gw3 (what the app sees):", json.dumps(fp))
