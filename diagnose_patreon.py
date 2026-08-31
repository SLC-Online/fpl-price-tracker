#!/usr/bin/env python3
"""Diagnostic: dump the full structure of the latest Transfer Algorithm post
so we can see exactly how the CSV attachment is delivered."""
import requests, os, json

PATREON_SESSION = os.environ.get("PATREON_SESSION", "")
CAMPAIGN_ID = "1982496"

HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Cookie': f'session_id={PATREON_SESSION}',
}

# Find latest post
url = f"https://www.patreon.com/api/posts?filter[campaign_id]={CAMPAIGN_ID}&filter[is_draft]=false&sort=-published_at&page[count]=5"
resp = requests.get(url, headers=HEADERS, timeout=15)
posts = resp.json().get('data', [])

post_id = None
for post in posts:
    title = post.get('attributes', {}).get('title', '')
    if 'transfer algorithm' in title.lower() and 'my team' not in title.lower():
        post_id = post['id']
        print(f"Post: {title} (id={post_id})")
        break

if not post_id:
    print("No post found")
    exit(1)

# Fetch with ALL possible includes
includes = "attachments,attachments_media,media,images,audio"
fields = "fields[post]=title,content,post_file,embed,post_metadata"
url = f"https://www.patreon.com/api/posts/{post_id}?include={includes}&{fields}"
resp = requests.get(url, headers=HEADERS, timeout=15)
print(f"Status: {resp.status_code}")

data = resp.json()

# Dump post attributes
attrs = data.get('data', {}).get('attributes', {})
print("\n=== POST ATTRIBUTES KEYS ===")
print(list(attrs.keys()))
print("\n=== post_file ===")
print(json.dumps(attrs.get('post_file'), indent=2))
print("\n=== post_metadata ===")
print(json.dumps(attrs.get('post_metadata'), indent=2)[:1000])

# Dump relationships
rels = data.get('data', {}).get('relationships', {})
print("\n=== RELATIONSHIPS KEYS ===")
print(list(rels.keys()))
for k, v in rels.items():
    print(f"  {k}: {json.dumps(v)[:200]}")

# Dump included items
included = data.get('included', [])
print(f"\n=== INCLUDED ITEMS ({len(included)}) ===")
for item in included:
    itype = item.get('type')
    iattrs = item.get('attributes', {})
    print(f"  type={itype}")
    print(f"    keys: {list(iattrs.keys())}")
    if 'name' in iattrs:
        print(f"    name: {iattrs.get('name')}")
    if 'file_name' in iattrs:
        print(f"    file_name: {iattrs.get('file_name')}")
    if 'download_url' in iattrs:
        print(f"    download_url: {iattrs.get('download_url', '')[:80]}")
    if 'url' in iattrs:
        print(f"    url: {iattrs.get('url', '')[:80]}")
