#!/usr/bin/env python3
import requests, os, json
U = os.environ["SUPABASE_URL"]; K = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": K, "Authorization": f"Bearer {K}"}
r = requests.get(f"{U}/rest/v1/final_projections?select=element_id,gameweek,expected_points,meta&gameweek=eq.3&limit=5", headers=H, timeout=15)
print("status", r.status_code)
for row in r.json():
    print(f"  el={row['element_id']} gw={row['gameweek']} meta={row['meta']!r} (type {type(row['meta']).__name__})")
