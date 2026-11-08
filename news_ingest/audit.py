"""Audit each workbook entry; an endpoint's HTTP 200 alone is not a working parser."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import feedparser

from .http import Client, atomic_json, utcnow

ROOT = Path(__file__).resolve().parents[1]


def catalogue():
    return json.loads((ROOT / "news_ingest" / "sources.json").read_text())


def audit_one(source):
    directory = ROOT / "data" / source["id"]
    directory.mkdir(parents=True, exist_ok=True)
    out = {"id": source["id"], "name": source["name"], "workbook_row": source["workbook_row"],
           "checked_at": utcnow(), "scope": source["scope"], "routes": [], "feeds": [],
           "apis": [], "sitemaps": [], "listing": source["home"], "collector_verified": False,
           "api_note": source["api_note"], "full_window_coverage": "not_established"}
    if source["scope"] == "excluded":
        out.update(status="excluded_by_user", reason=source["exclusion_reason"])
        atomic_json(directory / "audit.json", out)
        return out
    client = Client(directory)

    def probe(url, kind):
        rec, body = client.get(url, kind, ttl=300)
        item = {**rec, "kind": kind}
        out["routes"].append(item)
        return item, body

    rec, body = probe(source["home"], "html_listing")
    home_ok = rec.get("status") == 200
    soup = BeautifulSoup(body, "lxml") if rec.get("status") == 200 else BeautifulSoup("", "lxml")
    out["home_title"] = soup.title.get_text(" ", strip=True) if soup.title else ""
    feed_urls = list(source["feed_candidates"])
    for link in soup.select('link[rel="alternate"][href]'):
        if any(k in link.get("type", "") for k in ("rss", "atom")):
            feed_urls.append(urljoin(source["home"], link["href"]))
    for url in dict.fromkeys(feed_urls):
        if "comment" in url.lower():
            continue
        rec, data = probe(url, "rss")
        feed = feedparser.parse(data) if rec.get("status") == 200 else None
        count = len(feed.entries) if feed else 0
        rec["entries"] = count
        if count:
            rec["feed_title"] = feed.feed.get("title", "")
            rec["sample_urls"] = [e.get("link", "") for e in feed.entries[:3]]
            out["feeds"].append(url)
    for url in source["api_candidates"]:
        rec, data = probe(url + "?per_page=1", "public_news_api")
        try:
            entries = json.loads(data) if rec.get("status") == 200 else []
        except ValueError:
            entries = []
        valid = isinstance(entries, list) and bool(entries) and isinstance(entries[0], dict) and "content" in entries[0]
        rec["article_api_confirmed"] = valid
        if valid:
            out["apis"].append(url)
    origin = f"{urlsplit(source['home']).scheme}://{urlsplit(source['home']).netloc}"
    _, robots_text = client.robots.get(origin, (None, ""))
    maps = list(source["sitemap_candidates"])
    for line in robots_text.splitlines():
        if line.lower().startswith("sitemap:"):
            maps.append(line.split(":", 1)[1].strip())
    for url in list(dict.fromkeys(maps))[:4]:
        rec, data = probe(url, "sitemap")
        if rec.get("status") == 200 and (b"<urlset" in data or b"<sitemapindex" in data):
            out["sitemaps"].append(url)
    # Public article links, not every navigation URL, are inspected by the collector.
    out["listing_links"] = list(dict.fromkeys(urljoin(source["home"], a["href"])
                                              for a in soup.select('a[href]')))[:1000]
    if out["apis"] or out["feeds"] or out["sitemaps"] or home_ok:
        out.update(status="routes_found", reason="Article extraction and incremental rerun still need verification")
    else:
        errors = sorted({str(r.get("status") or r.get("error") or "unknown") for r in out["routes"]})
        out.update(status="no_working_route", reason=", ".join(errors))
    if source["kind"] == "social":
        out.update(status="requires_social_api", reason="No anonymous reusable text-post route verified; X API requires authorised access. No paid access requested.")
        out["api_note"] = "Official X API: https://docs.x.com/x-api/posts/search/introduction ; no credentialed request made."
    atomic_json(directory / "audit.json", out)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="*")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    sources = [s for s in catalogue() if not args.sources or s["id"] in args.sources]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(audit_one, sources):
            print(json.dumps({k: result.get(k) for k in ("id", "status", "feeds", "apis", "sitemaps", "reason")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
