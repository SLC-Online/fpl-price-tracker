#!/usr/bin/env python3
"""Diagnostic: clear the last-import timestamp so the next scraper run does a
full re-import, letting us verify csv_imports + projection_inputs writes."""
import requests, os, json

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# Clear the stored timestamp in projection_sources.meta
rows = requests.get(
    f"{SUPABASE_URL}/rest/v1/projection_sources?source_name=eq.transfer_algorithm&select=id,meta",
    headers=HEADERS, timeout=15
).json()
if rows:
    meta = rows[0].get('meta') or {}
    if isinstance(meta, str):
        try: meta = json.loads(meta)
        except: meta = {}
    meta.pop('last_patreon_published_at', None)
    sid = rows[0]['id']
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/projection_sources?id=eq.{sid}",
        headers=HEADERS, json={'meta': meta}, timeout=15
    )
    print(f"Cleared timestamp: {r.status_code}")
else:
    print("No transfer_algorithm source found")
