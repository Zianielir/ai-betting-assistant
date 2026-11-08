"""Incremental collector for Manchester Evening News.

Latest: python data/manchester_evening_news/collect.py
Backfill: python data/manchester_evening_news/collect.py --start 2026-10-26 --end 2026-11-09 --archive
Requires the project requirements.txt; shared implementation: news_ingest/.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from news_ingest.collector import run

if __name__ == "__main__":
    run('manchester_evening_news')
