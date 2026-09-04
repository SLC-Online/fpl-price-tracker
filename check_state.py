#!/usr/bin/env python3
import requests, os, json
U=os.environ["SUPABASE_URL"]; K=os.environ["SUPABASE_SERVICE_KEY"]
H={"apikey":K,"Authorization":f"Bearer {K}"}
def g(q):
    r=requests.get(f"{U}/rest/v1/{q}",headers=H,timeout=20); return r.status_code, r.json()

# sources
sc,src=g("projection_sources?select=id,source_name")
print("SOURCES:",src)

# captures WITH source_id + meta, newest first
sc,caps=g("projection_captures?select=id,source_id,uploaded_for_gw,captured_at,row_count,player_count,meta&order=captured_at.desc&limit=12")
print("\nCAPTURES (newest first) — note source_id + meta:")
for c in caps if isinstance(caps,list) else []:
    print(f"  id={c['id']} src={c['source_id']} gw={c['uploaded_for_gw']} rows={c['row_count']} players={c['player_count']} at={c['captured_at'][:19]} meta={c['meta']}")

# what does final_projections join on? show its definition indirectly:
# check: which source_names does final_projections include? Query distinct via a sample
sc,fp=g("final_projections?select=element_id&limit=1")
print("\nfinal_projections sample status:",sc)
