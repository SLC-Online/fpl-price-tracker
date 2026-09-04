#!/usr/bin/env python3
import requests, os, json
U=os.environ["SUPABASE_URL"]; K=os.environ["SUPABASE_SERVICE_KEY"]
H={"apikey":K,"Authorization":f"Bearer {K}"}
def g(q):
    r=requests.get(f"{U}/rest/v1/{q}",headers=H,timeout=20); return r.status_code, r.json()
sc,fp=g("final_projections?select=gameweek,expected_points,uploaded_for_gw&element_id=eq.411&order=gameweek")
print("final_projections Haaland (all rows):")
for r in fp: print(f"  gw{r['gameweek']} = {r['expected_points']}  (uploaded_for_gw={r['uploaded_for_gw']})")
