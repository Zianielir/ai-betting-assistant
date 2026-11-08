"""Create entry points only after real article extraction has succeeded."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from news_ingest.audit import catalogue
from news_ingest.collector import load_records, article_url
from news_ingest.http import atomic_json
from news_ingest.parsing import canonical

for source in catalogue():
    directory = ROOT / "data" / source["id"]
    audit = json.loads((directory / "audit.json").read_text())
    feed_urls = {}
    for url in audit.get('feeds', []):
        if 'comment' in url.lower():
            continue
        route = next((r for r in audit.get('routes', []) if r.get('entries', 0) and (r.get('requested_url') == url or r.get('url') == url)), {})
        endpoint = route.get('url', url)
        feed_urls.setdefault(canonical(endpoint), endpoint)
    audit['feeds'] = list(feed_urls.values())
    atomic_json(directory/'audit.json', audit)
    rows = load_records(directory)
    partial_article_feed = bool(audit.get('feeds')) and any(r.get('text_status')=='partial' and r.get('published_at') and not r.get('exclusion_reason') and article_url(r['url'], source) for r in rows.values())
    if not audit.get("collector_verified") and not partial_article_feed:
        continue
    audit['entrypoint_mode'] = 'full_text_available' if audit.get('collector_verified') else 'metadata_and_excerpt_only'
    if not audit.get('collector_verified'):
        audit.update(status='partial_collector_verified',reason='Public author feed provides dated article URLs and excerpts; full-text access not verified. Saved separately in partial.csv.')
    atomic_json(directory/'audit.json', audit)
    script = directory / "collect.py"
    script.write_text('''"""Incremental collector for %s.

Latest: python data/%s/collect.py
Backfill: python data/%s/collect.py --start 2026-10-26 --end 2026-11-09 --archive
Requires the project requirements.txt; shared implementation: news_ingest/.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from news_ingest.collector import run

if __name__ == "__main__":
    run(%r)
''' % (source["name"], source["id"], source["id"], source["id"]))
    print(source["id"])
