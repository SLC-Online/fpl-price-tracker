#!/usr/bin/env python3
"""
Populate the projections layer from existing csv_imports data.
Transforms the CSV's gw1-gw8 columns into the normalised projection_inputs table.

Run after uploading a new CSV, or run once to backfill from existing imports.
Requires: SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables.
"""
import os, requests, json

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def supabase_get(path):
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        raise Exception(f"GET {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def supabase_post(table, data, upsert_cols=None):
    headers = dict(HEADERS)
    if upsert_cols:
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={upsert_cols}"
    else:
        headers["Prefer"] = "return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}"

    for i in range(0, len(data), 200):
        chunk = data[i:i + 200]
        resp = requests.post(url, headers=headers, json=chunk, timeout=30)
        if resp.status_code not in (200, 201, 204):
            raise Exception(f"POST {table}: {resp.status_code} {resp.text[:200]}")


def populate():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY")
        return

    # Get the Transfer Algorithm source ID
    sources = supabase_get("projection_sources?source_name=eq.transfer_algorithm&select=id")
    if not sources:
        print("No 'transfer_algorithm' source found. Run migration 002 first.")
        return
    source_id = sources[0]['id']
    print(f"Transfer Algorithm source ID: {source_id}")

    # Get all CSV imports
    imports = supabase_get("csv_imports?select=element_id,season,gameweek,gw1,gw2,gw3,gw4,gw5,gw6,gw7,gw8,bcv,projected_sum,ppg_longer_term&order=gameweek")
    print(f"CSV imports to process: {len(imports)}")

    # Transform into projection_inputs rows
    projections = []
    for row in imports:
        season = row['season']
        uploaded_for_gw = row['gameweek']  # CSV was uploaded before this GW
        gws = [row['gw1'], row['gw2'], row['gw3'], row['gw4'],
               row['gw5'], row['gw6'], row['gw7'], row['gw8']]

        for i, pts in enumerate(gws):
            if pts is None:
                continue
            actual_gw = uploaded_for_gw + i  # gw1 column = the GW the CSV targets
            if actual_gw > 38:
                continue

            projections.append({
                "source_id": source_id,
                "element_id": row['element_id'],
                "season": season,
                "gameweek": actual_gw,
                "uploaded_for_gw": uploaded_for_gw,
                "expected_points": pts,
                "meta": json.dumps({
                    "bcv": row.get('bcv'),
                    "projected_sum": row.get('projected_sum'),
                    "ppg_longer_term": row.get('ppg_longer_term'),
                }),
            })

    print(f"Projection rows to insert: {len(projections)}")
    supabase_post("projection_inputs", projections,
                  upsert_cols="source_id,element_id,season,gameweek,uploaded_for_gw")
    print("✓ Done")


if __name__ == "__main__":
    populate()
