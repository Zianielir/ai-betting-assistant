"""Invoke every final .py entry point again and verify append/upsert invariants."""
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def urls(directory):
    path = directory / "records.jsonl"
    return {json.loads(line)["url"] for line in path.read_text().splitlines()} if path.exists() else set()


def check(script):
    directory = script.parent
    before = urls(directory)
    start_clock = time.monotonic()
    process = subprocess.run([sys.executable, str(script), "--start", "2026-10-26", "--end", "2026-11-09"],
                             cwd=ROOT, capture_output=True, text=True, timeout=600)
    after = urls(directory)
    duplicates = {}
    text_roundtrip = True
    records = {r["url"]: r for r in (json.loads(line) for line in (directory / "records.jsonl").read_text().splitlines())}
    for name in ("news", "partial", "review", "excluded", "posts"):
        with (directory / (name + ".csv")).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        duplicates[name] = len(rows) - len({r["url"] for r in rows})
        text_roundtrip &= all(row["text"] == records[row["url"]]["text"] for row in rows)
    result = {"source_id": directory.name, "script": str(script.relative_to(ROOT)),
              "duration_seconds": round(time.monotonic() - start_clock, 3),
              "returncode": process.returncode, "before": len(before), "after": len(after),
              "new_urls": len(after - before), "old_urls_lost": len(before - after),
              "csv_duplicate_urls": duplicates, "csv_text_roundtrip": text_roundtrip,
              "passed": process.returncode == 0 and not before - after and not any(duplicates.values()) and text_roundtrip}
    if process.returncode:
        result["stderr"] = process.stderr[-1500:]
    (directory / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check, sorted((ROOT / "data").glob("*/collect.py"))))
    (ROOT / "research" / "rerun_checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    if not all(r["passed"] for r in results):
        raise SystemExit(1)
