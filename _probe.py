import os, requests, json
S=os.environ["PATREON_SESSION"]
H={'User-Agent':'Mozilla/5.0','Cookie':f'session_id={S}'}
CID="1982496"
# request edited_at + published_at explicitly
url=(f"https://www.patreon.com/api/posts?filter[campaign_id]={CID}"
     f"&filter[is_draft]=false&sort=-published_at&page[count]=5"
     f"&fields[post]=title,published_at,edited_at,change_visibility_at")
r=requests.get(url,headers=H,timeout=20)
print("status",r.status_code)
d=r.json()
for p in d.get('data',[]):
    a=p.get('attributes',{})
    if 'transfer algorithm' in a.get('title','').lower():
        print(json.dumps({'id':p['id'],**a},indent=2)[:600])
