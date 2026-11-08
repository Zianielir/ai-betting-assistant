"""Reapply final extraction/filter rules to saved snapshots, without network requests."""
from pathlib import Path
import gzip
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from news_ingest.audit import catalogue
from news_ingest.collector import export, load_records, source_lock
from news_ingest.http import atomic_json
from news_ingest.parsing import extract, selection

aliases = json.loads((ROOT / "news_ingest" / "club_aliases.json").read_text())["aliases"]
for source in catalogue():
    directory = ROOT / "data" / source["id"]
    if not (directory / "records.jsonl").exists():
        continue
    with source_lock(directory):
        records = load_records(directory)
        revised = {}
        audit_before = json.loads((directory/'audit.json').read_text())
        for row in records.values():
            raw = directory / row.get("raw_path", "missing")
            body = gzip.decompress(raw.read_bytes()) if row.get("raw_path") and raw.is_file() else b""
            hint = {"title":row["title"], "published_at":row.get("published_at", ""),
                    "date_provenance": "rss_or_atom.published" if row.get('discovered_via') in audit_before.get('feeds', []) else 'wordpress.date_gmt' if any(row.get('discovered_via', '').startswith(u) for u in audit_before.get('apis', [])) else row.get('date_provenance', 'unknown'),
                    "summary":row.get("text", "") if row.get("text_status")=="partial" else "",
                    "text":row.get("text", "") if row.get("extraction_method")=="feed_or_api_content" else "",
                    "tags":row.get("source_tags", []) if row.get("extraction_method")=="feed_or_api_content" or not body else []}
            new = extract(body, row["url"], source["id"], hint)
            if source["id"] == "rotowire" and "/soccer/article/" not in row["url"]:
                new["exclusion_reason"] = "player_profile_is_not_a_news_article"
            if not body:
                new["text_status"] = "partial"
            status, reason, clubs = selection(new, aliases)
            if not new["published_at"]:
                status, reason = "review", "missing_or_timezone_ambiguous_publication_date"
            revised[new["url"]] = {**row, **new, "clubs":clubs,"selection_status":status,"selection_reason":reason}
        counts = export(directory, revised)
        p = directory / "audit.json"
        audit = json.loads(p.read_text())
        audit["saved_counts"] = counts
        valid = [r for r in revised.values() if r.get('text_status')=='full_text' and not r.get('exclusion_reason')]
        audit['collector_verified'] = bool(valid)
        if not valid and revised:
            if all(r.get('exclusion_reason')=='non_english' for r in revised.values()):
                audit.update(status='non_english_only', reason='Public routes return Italian articles; no English route verified.')
            elif source['id']=='rotowire':
                audit.update(status='feed_only_no_articles',reason='RSS links lead to player profiles, not distinct news articles. Profile text is excluded; no reusable full-article collector verified.')
        atomic_json(p, audit)
        print(source["id"], counts, flush=True)
