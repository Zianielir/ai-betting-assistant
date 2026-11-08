"""Incremental collector for Sky Sports.

Latest: python data/sky_sports/collect.py
Backfill: python data/sky_sports/collect.py --start 2026-10-26 --end 2026-11-09 --archive
Requires the project requirements.txt; shared implementation: news_ingest/.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from news_ingest.collector import run

if __name__ == "__main__":
    run('sky_sports')
