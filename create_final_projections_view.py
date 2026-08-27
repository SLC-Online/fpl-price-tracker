#!/usr/bin/env python3
"""
Create the 'final_projections' view in Supabase.

This view provides ONE definitive expected points number per player per gameweek.
Currently it passes through Transfer Algorithm values only.
To blend sources later, modify this view's SQL — all downstream queries use it automatically.
"""
import requests, os

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

SQL = """
CREATE OR REPLACE VIEW final_projections AS
SELECT
    pi.element_id,
    pi.gameweek,
    pi.expected_points,
    pi.uploaded_for_gw,
    pi.season,
    pi.meta
FROM projection_inputs pi
JOIN projection_sources ps ON ps.id = pi.source_id
WHERE ps.source_name = 'transfer_algorithm';
"""

# Execute via Supabase's RPC (requires a helper function) or use the REST SQL endpoint
# Supabase doesn't expose raw SQL via REST, so we use the pg_net extension or 
# create via supabase management API. Simplest: use the SQL editor via REST.

# Actually, use the supabase management API
# But we can just use the postgrest rpc if we have a function...
# Simplest approach: use the Supabase SQL API (requires service key)

resp = requests.post(
    f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
    headers=HEADERS,
    json={"query": SQL},
    timeout=30
)

if resp.status_code in (200, 204):
    print("✓ Created final_projections view")
else:
    # If exec_sql doesn't exist, try creating it first
    print(f"exec_sql RPC failed ({resp.status_code}): {resp.text[:200]}")
    print("Trying alternative approach...")
    
    # Create the exec_sql function first
    create_fn = """
    CREATE OR REPLACE FUNCTION exec_sql(query text) RETURNS void AS $$
    BEGIN
        EXECUTE query;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER;
    """
    
    # Can't bootstrap without existing RPC... use the Supabase Dashboard SQL editor
    # Let's try the /pg endpoint if available
    resp2 = requests.post(
        f"{SUPABASE_URL}/pg/query",
        headers=HEADERS,
        json={"query": SQL},
        timeout=30
    )
    if resp2.status_code in (200, 204):
        print("✓ Created final_projections view via /pg endpoint")
    else:
        print(f"Alternative also failed ({resp2.status_code})")
        print("\nYou need to run this SQL in the Supabase Dashboard SQL Editor:")
        print("=" * 60)
        print(SQL)
        print("=" * 60)
        print("\nThen grant access:")
        print("GRANT SELECT ON final_projections TO anon, authenticated;")
