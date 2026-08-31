#!/usr/bin/env python3
"""One-off: verify BCV in final_projections and remove any empty/orphaned captures."""
import requests, os, json

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
HW = {**H, "Content-Type": "application/json", "Prefer": "return=minimal"}


def get(q):
    return requests.get(f"{SUPABASE_URL}/rest/v1/{q}", headers=H, timeout=20).json()


# 1. Show all TA captures with their row counts
caps = get("projection_captures?select=id,uploaded_for_gw,captured_at,row_count,content_hash&source_id=eq.1&order=captured_at.desc")
print("Transfer Algorithm captures:")
for c in caps if isinstance(caps, list) else []:
    print(f"  id={c['id']} gw={c['uploaded_for_gw']} rows={c['row_count']} hash={c['content_hash'][:14]} at={c['captured_at'][:19]}")

# 2. Find captures with NO projection_inputs rows (orphans) and delete them
if isinstance(caps, list):
    for c in caps:
        rows = get(f"projection_inputs?capture_id=eq.{c['id']}&select=id&limit=1")
        if not (isinstance(rows, list) and rows):
            print(f"  -> orphan capture {c['id']} (no rows), deleting")
            requests.delete(f"{SUPABASE_URL}/rest/v1/projection_captures?id=eq.{c['id']}", headers=HW, timeout=15)

# 3. Verify BCV distribution in final_projections (next GW only)
fp = get("final_projections?select=element_id,expected_points,meta&gameweek=eq.3&limit=600")
if isinstance(fp, list):
    bcvs = []
    for r in fp:
        m = r.get('meta')
        if isinstance(m, str):
            try: m = json.loads(m)
            except: m = {}
        if isinstance(m, dict) and m.get('bcv') is not None:
            bcvs.append(m['bcv'])
    if bcvs:
        neg = sum(1 for b in bcvs if b < 0)
        print(f"\nfinal_projections GW3: {len(bcvs)} players with BCV")
        print(f"  range {min(bcvs):.2f} .. {max(bcvs):.2f}, {neg} negative")
        print(f"  sample: {sorted(bcvs)[:5]} ... {sorted(bcvs)[-5:]}")
    else:
        print("\nNo BCV values found in final_projections GW3")
