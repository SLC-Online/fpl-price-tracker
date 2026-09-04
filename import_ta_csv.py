#!/usr/bin/env python3
"""
Direct, verified Transfer Algorithm CSV importer.

Bypasses the flaky admin endpoint. Reads the CSV, name-matches to FPL element
ids (same matcher the optimizer uses), and writes a fresh source-1
projection_capture + projection_inputs rows. Content-hash dedup: skips if the
data is identical to the latest source-1 capture.

Usage (GitHub Actions passes CSV via repo or you paste path locally):
    SUPABASE_URL=.. SUPABASE_SERVICE_KEY=.. python3 import_ta_csv.py <csv_path> <uploaded_for_gw>
"""
import sys, os, csv, hashlib, json, requests
from io import StringIO
import optimize_from_csv as C
import optimizer_app as O

U = os.environ["SUPABASE_URL"]; K = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": K, "Authorization": f"Bearer {K}", "Content-Type": "application/json"}

csv_path = sys.argv[1]
uploaded_for_gw = int(sys.argv[2])
season = "2026-27"

bs = O.get_bootstrap()
players, all_gws, unmatched = C.load_csv_projections(csv_path, bs)
print(f"CSV parsed: {len(players)} matched players, {len(unmatched)} unmatched, GWs {all_gws}")

# source id
r = requests.get(f"{U}/rest/v1/projection_sources?source_name=eq.transfer_algorithm&select=id", headers=H)
source_id = r.json()[0]["id"]

# build projection rows: each player's absolute-GW projections
proj_rows = []
seen = set()
for p in players.values():
    for gw, pts in p.projections.items():
        key = (p.element_id, gw)
        if key in seen:
            continue
        seen.add(key)
        proj_rows.append({
            "source_id": source_id, "element_id": p.element_id, "season": season,
            "gameweek": gw, "uploaded_for_gw": uploaded_for_gw,
            "expected_points": round(pts, 3),
            "meta": json.dumps({"bcv": getattr(p, "bcv", None)}),
        })

# content hash for dedup
items = sorted((r["element_id"], r["gameweek"], r["expected_points"]) for r in proj_rows)
chash = hashlib.sha256(";".join(f"{e}:{g}:{v}" for e, g, v in items).encode()).hexdigest()

prev = requests.get(
    f"{U}/rest/v1/projection_captures?source_id=eq.{source_id}&uploaded_for_gw=eq.{uploaded_for_gw}"
    f"&season=eq.{season}&select=content_hash&order=captured_at.desc&limit=1", headers=H).json()
if isinstance(prev, list) and prev and prev[0].get("content_hash") == chash:
    print("Identical to latest capture — nothing to import.")
    sys.exit(0)

player_count = len({r["element_id"] for r in proj_rows})
cap = requests.post(f"{U}/rest/v1/projection_captures",
    headers={**H, "Prefer": "return=representation"},
    json={"source_id": source_id, "season": season, "uploaded_for_gw": uploaded_for_gw,
          "content_hash": chash, "row_count": len(proj_rows), "player_count": player_count,
          "meta": json.dumps({"source": "direct_import"})}, timeout=20)
if cap.status_code not in (200, 201):
    print(f"ERROR creating capture: {cap.status_code} {cap.text[:300]}")
    sys.exit(1)
capture_id = cap.json()[0]["id"]
print(f"Created capture {capture_id}: {len(proj_rows)} rows, {player_count} players")

rows = [{**r, "capture_id": capture_id} for r in proj_rows]
ok = 0
for i in range(0, len(rows), 200):
    resp = requests.post(f"{U}/rest/v1/projection_inputs",
        headers={**H, "Prefer": "return=minimal"}, json=rows[i:i+200], timeout=30)
    if resp.status_code in (200, 201, 204):
        ok += len(rows[i:i+200])
    else:
        print(f"  row insert error: {resp.status_code} {resp.text[:200]}")
print(f"Inserted {ok}/{len(rows)} projection_inputs rows under capture {capture_id}")

# verify Haaland
hz = requests.get(f"{U}/rest/v1/projection_inputs?select=expected_points&element_id=eq.411&gameweek=eq.3&capture_id=eq.{capture_id}", headers=H).json()
print("Haaland GW3 in new capture:", hz)
