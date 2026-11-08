"""Check actual saved data, not just mocked parser examples (no network)."""
from pathlib import Path
import csv
import gzip
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from news_ingest.audit import catalogue
from news_ingest.collector import load_records
from news_ingest.parsing import date

errors, counts, sources = [], {}, []
start, end = date('2026-10-25T21:00:00Z'), date('2026-11-08T21:00:00Z')
for source in catalogue():
    directory = ROOT/'data'/source['id']
    assert (directory/'audit.json').exists(), source['id']
    records = load_records(directory)
    seen = set()
    local_counts = {}
    for group in ('news','posts','partial','review','excluded'):
        path = directory/(group+'.csv')
        if not path.exists():
            if records:
                errors.append([source['id'], 'missing_csv', group])
            continue
        with path.open(encoding='utf-8-sig',newline='') as handle:
            rows = list(csv.DictReader(handle))
        local_counts[group] = len(rows)
        counts[group] = counts.get(group,0)+len(rows)
        keys = [r['url'] for r in rows]
        if len(keys) != len(set(keys)) or seen.intersection(keys):
            errors.append([source['id'], group, 'duplicate_or_cross_group_url'])
        seen.update(keys)
        for row in rows:
            if row['text'] != records[row['url']]['text']:
                errors.append([source['id'], row['url'], 'csv_text_mismatch'])
            if hashlib.sha256(row['text'].encode()).hexdigest() != row['content_hash']:
                errors.append([source['id'], row['url'], 'content_hash_mismatch'])
            for field in ('authors','publisher','source_tags','clubs'):
                if not isinstance(json.loads(row[field]),list):
                    errors.append([source['id'], row['url'], 'invalid_list',field])
            if group=='news':
                pub=date(row['published_at'])
                if not pub or not start <= pub < end:
                    errors.append([source['id'],row['url'],'outside_requested_window'])
                if row['text_status']!='full_text' or row['selection_status']!='included' or row['content_type']!='article' or row['exclusion_reason']:
                    errors.append([source['id'],row['url'],'invalid_news_group'])
            if row.get('raw_path'):
                raw=directory/row['raw_path']
                meta=raw.with_suffix('.json')
                try:
                    checksum=hashlib.sha256(gzip.decompress(raw.read_bytes())).hexdigest()
                    if checksum != json.loads(meta.read_text())['sha256']:
                        errors.append([source['id'],row['url'],'raw_checksum_mismatch'])
                except (OSError,EOFError,ValueError,KeyError):
                    errors.append([source['id'],row['url'],'raw_missing_or_invalid'])
    if seen != set(records):
        errors.append([source['id'],'csv_master_partition_mismatch'])
    sources.append({'source_id':source['id'],'records':len(records),'counts':local_counts,'script':(directory/'collect.py').exists()})
result={'all_checks_passed':not errors,'source_rows':len(sources),'counts':counts,'sources':sources,'errors':errors,
        'window':'[2026-10-26 00:00, 2026-11-09 00:00) Europe/Moscow; current frozen initial collection only'}
(ROOT/'research'/'quality_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('sources','errors')},ensure_ascii=False))
if errors:
    print(json.dumps(errors[:12],ensure_ascii=False))
    raise SystemExit(1)
