#!/usr/bin/env python3
"""Verify the capture system: final_projections returns data, and captures exist."""
import requests, os, json

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

def get(q):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{q}", headers=HEADERS, timeout=15)
    return r.status_code, r.json()

# 1. final_projections must return rows (this is what the app reads)
sc, rows = get("final_projections?select=element_id,gameweek,expected_points&limit=5")
print(f"final_projections: {sc}, sample: {json.dumps(rows)[:300]}")

sc, cnt = get("final_projections?select=element_id")
print(f"final_projections total rows: {len(cnt) if isinstance(cnt, list) else cnt}")

# 2. Captures per source
sc, caps = get("projection_captures?select=source_id,uploaded_for_gw,captured_at,row_count,player_count,content_hash&order=captured_at.desc&limit=30")
print(f"\nprojection_captures ({sc}): {len(caps) if isinstance(caps,list) else caps} shown")
if isinstance(caps, list):
    for c in caps:
        h = c.get('content_hash', '')[:16]
        print(f"  src={c['source_id']} gw={c['uploaded_for_gw']} rows={c['row_count']} players={c['player_count']} hash={h} at={c['captured_at'][:19]}")

# 3. Sources
sc, srcs = get("projection_sources?select=id,source_name")
print(f"\nsources: {srcs}")
