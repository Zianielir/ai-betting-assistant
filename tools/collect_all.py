"""Collect audited sources; each source's .py entry point uses the same implementation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import sys
import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from news_ingest.audit import catalogue
from news_ingest.collector import collect, STOP
from news_ingest.http import atomic_json, utcnow

signal.signal(signal.SIGINT, lambda *_: STOP.set())
signal.signal(signal.SIGTERM, lambda *_: STOP.set())

parser = argparse.ArgumentParser()
parser.add_argument("--sources", nargs="*")
parser.add_argument("--start")
parser.add_argument("--end")
parser.add_argument("--archive", action="store_true")
parser.add_argument("--workers", type=int, default=6)
parser.add_argument("--max-pages", type=int, default=120)
parser.add_argument("--max-articles", type=int)
args = parser.parse_args()

sources = []
for source in catalogue():
    path = ROOT / "data" / source["id"] / "audit.json"
    if source["scope"] != "news" or args.sources and source["id"] not in args.sources:
        continue
    if path.exists() and json.loads(path.read_text()).get("status") in ("requires_social_api", "no_working_route"):
        continue
    sources.append(source)
failures = []
with ThreadPoolExecutor(max_workers=args.workers) as pool:
    futures = {pool.submit(collect, s["id"], start=args.start, end=args.end, archive=args.archive,
                           max_pages=args.max_pages, max_articles=args.max_articles): s["id"] for s in sources}
    for future in as_completed(futures):
        try:
            future.result()
        except Exception as error:
            failure = {"source": futures[future], "collector_error": type(error).__name__, "message": str(error)[:200], "at": utcnow()}
            failures.append(failure)
            print(json.dumps(failure), flush=True)
            audit_file = ROOT/'data'/futures[future]/'audit.json'
            if audit_file.exists():
                audit = json.loads(audit_file.read_text())
                audit['last_collection_error'] = failure
                atomic_json(audit_file,audit)
atomic_json(ROOT/'research'/'last_batch_failures.json', failures)
if failures:
    raise SystemExit(1)
