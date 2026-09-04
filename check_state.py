#!/usr/bin/env python3
import requests, os, json
U=os.environ["SUPABASE_URL"]; K=os.environ["SUPABASE_SERVICE_KEY"]
H={"apikey":K,"Authorization":f"Bearer {K}"}
def g(q): 
    r=requests.get(f"{U}/rest/v1/{q}",headers=H,timeout=20); return r.status_code, r.json()

# 1. all captures for transfer_algorithm, newest first
sc,caps=g("projection_captures?select=id,uploaded_for_gw,captured_at,row_count,player_count,content_hash&order=captured_at.desc&limit=15")
print("CAPTURES (newest first):",sc)
for c in caps if isinstance(caps,list) else []:
    print(f"  id={c['id']} gw={c['uploaded_for_gw']} rows={c['row_count']} players={c['player_count']} at={c['captured_at'][:19]} hash={str(c['content_hash'])[:16]}")

# 2. Haaland (id 355? find) in final_projections for gw3
sc,ha=g("final_projections?select=element_id,gameweek,expected_points,uploaded_for_gw,meta&element_id=eq.351&gameweek=eq.3")
print("\nfinal_projections Haaland-ish (el351) gw3:",sc,json.dumps(ha)[:300])

# find haaland id via players table
sc,p=g("players?select=element_id,web_name&web_name=eq.Haaland")
print("Haaland id lookup:",p)
if isinstance(p,list) and p:
    hid=p[0]['element_id']
    sc,fp=g(f"final_projections?select=element_id,gameweek,expected_points,uploaded_for_gw&element_id=eq.{hid}&order=gameweek")
    print(f"final_projections Haaland (id{hid}):",json.dumps(fp)[:400])
    sc,pi=g(f"projection_inputs?select=gameweek,expected_points,capture_id,uploaded_for_gw&element_id=eq.{hid}&gameweek=eq.3&order=capture_id.desc&limit=10")
    print(f"projection_inputs Haaland gw3 (all captures):",json.dumps(pi)[:500])
