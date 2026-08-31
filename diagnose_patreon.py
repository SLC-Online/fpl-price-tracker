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

resp = requests.get(
    f"{SUPABASE_URL}/rest/v1/projection_sources?source_name=eq.transfer_algorithm&select=*",
    headers=HEADERS, timeout=15
)
print(f"GET status: {resp.status_code}")
rows = resp.json()
print(f"Response: {json.dumps(rows)[:500]}")

if isinstance(rows, list) and rows:
    sid = rows[0]['id']
    base_desc = (rows[0].get('description') or '').split('|last_import=', 1)[0].rstrip()
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/projection_sources?id=eq.{sid}",
        headers=HEADERS, json={'description': base_desc}, timeout=15
    )
    print(f"Cleared timestamp: {r.status_code}")
else:
    print("Could not read projection_sources (see response above)")
