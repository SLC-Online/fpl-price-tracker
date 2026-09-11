import os, requests, json
U=os.environ["SUPABASE_URL"]; K=os.environ["SUPABASE_SERVICE_KEY"]
H={"apikey":K,"Authorization":f"Bearer {K}"}
def g(q): return requests.get(f"{U}/rest/v1/{q}",headers=H,timeout=20).json()

# 1. distinct player count in final_projections for the latest upload / gw4
rows=g("final_projections?select=element_id&gameweek=eq.4&uploaded_for_gw=eq.4")
ids=set(r['element_id'] for r in rows) if isinstance(rows,list) else set()
print("final_projections distinct players @ gw4/upload4:", len(ids), "(rows:", len(rows) if isinstance(rows,list) else rows, ")")

# 2. how many distinct players does the transfer_algorithm source alone have for gw4?
# find TA source id
src=g("projection_sources?select=id,source_name")
print("sources:", src)
ta=[s['id'] for s in src if s['source_name']=='transfer_algorithm'][0]
# latest TA capture
caps=g(f"projection_captures?select=id,uploaded_for_gw,player_count,captured_at&source_id=eq.{ta}&order=captured_at.desc&limit=3")
print("latest TA captures:", caps)

# 3. Does final_projections include rows whose capture is NOT a TA capture?
#    Get all capture_ids referenced by final_projections gw4, then check their source.
# final_projections doesn't expose capture_id, so instead: compare its player set
# to the TA-only player set for the same capture.
if caps:
    capid=caps[0]['id']
    ta_rows=g(f"projection_inputs?select=element_id&capture_id=eq.{capid}&gameweek=eq.4")
    ta_ids=set(r['element_id'] for r in ta_rows) if isinstance(ta_rows,list) else set()
    print("TA-only distinct players @ gw4 (latest capture):", len(ta_ids))
    print("players in final_projections but NOT in latest TA capture:", len(ids - ta_ids))
    extra=list(ids-ta_ids)[:10]
    print("  sample extras:", extra)
