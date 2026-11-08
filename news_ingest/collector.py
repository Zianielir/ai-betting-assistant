"""Shared implementation used by the working data/<source>/collect.py entry points."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import re
import sys
import signal
import threading
import time
from urllib.parse import urljoin, urlsplit, urlencode
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import feedparser
from lxml import etree

from .audit import ROOT, audit_one, catalogue
from .http import Client, atomic_json, utcnow
from .parsing import canonical, clean, date, extract, selection

MOSCOW = ZoneInfo("Europe/Moscow")
STOP = threading.Event()
FIELDS = ["document_id", "source", "source_id", "url", "title", "text", "content_type",
          "published_at", "published_at_raw", "published_date_display", "date_precision", "date_provenance", "modified_at", "fetched_at",
          "first_seen_at", "last_seen_at", "language", "authors", "publisher", "source_tags", "clubs",
          "selection_status", "selection_reason", "text_status", "word_count",
          "extraction_method", "content_hash", "discovered_via", "raw_path", "exclusion_reason"]


def boundary(value, default):
    if not value:
        return default
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=MOSCOW).astimezone(timezone.utc) if not dt.tzinfo else dt.astimezone(timezone.utc)


def article_url(url, source):
    p = urlsplit(url)
    home_host = urlsplit(source["home"]).netloc.removeprefix("www.")
    if not p.netloc.removeprefix("www.").endswith(home_host):
        return False
    path = p.path.lower()
    if any(x in path for x in ("/video/", "/videos/", "/live-blog/", "/tag/", "/tags/", "/author/", "/authors/", "/category/", "/wp-content/", "/comments/")):
        return False
    if re.search(r"\.(?:jpg|png|webp|xml|pdf|css|js)$", path):
        return False
    sid = source["id"]
    if sid == "sky_sports":
        return "/football/news/" in path
    if sid == "espn":
        return ("/soccer/" in path or "/football/" in path) and "/story/" in path
    if sid == "independent":
        return "/sport/football/" in path and path.endswith(".html")
    if source["kind"] == "reach":
        return bool(re.search(r"-\d{6,}(?:\.amp)?$", path)) and ("/sport/football/" in path or source["id"] == "football_london")
    if sid in ("the_argus", "bournemouth_echo"):
        return bool(re.search(r"/\d{6,}\.", path))
    if sid == "sports_mole":
        return "/football/" in path and bool(re.search(r"_\d+\.html$", path))
    if sid == "premier_league":
        return "/en/news/" in path and len(path.rstrip("/").split("/")) >= 4
    if sid == "the_fa":
        return "/news/" in path and len(path) > 30
    if sid == "goal":
        return "/en/news/" in path and "/blt" in path
    if sid == "fourfourtwo":
        return len(path) > 25 and path not in ("/features", "/news", "/news/")
    if sid == "fotmob":
        return "/news/" in path and len(path) > 15
    if sid == "livescore":
        return "/en/news/" in path and len(path) > 35 and not any(x in path for x in ('/team/', '/categories/', '/daily/', '/tennis/', '/cricket/', '/basketball/', '/hockey/', '/american-football/'))
    if sid == "rotowire":
        return "/soccer/article/" in path
    return len(path.strip("/").split("/")) >= 2 and len(path) > 25


def xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def discover(source, audit, client, start, end, archive, max_pages):
    found = {}
    diagnostics = {"feed_requests": 0, "api_pages": 0, "sitemap_pages": 0,
                   "listing_pages": 0, "limits": [], "routes_with_errors": []}

    def add(url, via, hint=None):
        if not url or urlsplit(url).scheme not in ("http", "https"):
            return
        key = canonical(url)
        previous = found.get(key, {})
        found[key] = {**previous, **(hint or {}), "fetch_url": url, "discovered_via": previous.get("discovered_via", via)}

    # These feeds are all polled again on a live run, using ETag/Last-Modified.
    for url in audit.get("feeds", []):
        if STOP.is_set():
            break
        if "comment" in url.lower():
            continue
        rec, data = client.get(url, "rss")
        diagnostics["feed_requests"] += 1
        if rec.get("status") != 200:
            diagnostics["routes_with_errors"].append({"url": url, "status": rec.get("status"), "error": rec.get("error")})
            continue
        feed = feedparser.parse(data)
        for entry in feed.entries:
            published = entry.get("published", "")
            # Atom updated is not a substitute for original publication time.
            content = entry.get("content", [])
            tags = [t.get("term", "") for t in entry.get("tags", [])]
            hint = {"title": entry.get("title", ""), "published_at": published,
                    "date_provenance": "rss_or_atom.published",
                    "summary": entry.get("summary", ""),
                    "text": "\n".join(c.get("value", "") for c in content),
                    "tags": tags, "language": feed.feed.get("language", "")}
            add(entry.get("link", ""), url, hint)
    # WordPress is a public news API, not an undocumented score endpoint.
    for url in audit.get("apis", []):
        for page in range(1, (max_pages if archive else 1) + 1):
            if STOP.is_set():
                break
            params = {"per_page": 100 if archive else 20, "page": page, "orderby": "date", "order": "desc", "_embed": "1"}
            params["after"] = start.isoformat().replace("+00:00", "Z")
            params["before"] = end.isoformat().replace("+00:00", "Z")
            rec, data = client.get(url + "?" + urlencode(params), "public_news_api")
            diagnostics["api_pages"] += 1
            try:
                posts = json.loads(data) if rec.get("status") == 200 else []
            except ValueError:
                posts = []
            if not isinstance(posts, list) or not posts:
                if rec.get("status") not in (200, 400):
                    diagnostics["routes_with_errors"].append({"url": url, "status": rec.get("status"), "error": rec.get("error")})
                break
            for post in posts:
                terms = [t.get("name", "") for group in post.get("_embedded", {}).get("wp:term", []) for t in group]
                hint = {"title": BeautifulSoup(post.get("title", {}).get("rendered", ""), "lxml").get_text(),
                        "date_provenance": "wordpress.date_gmt",
                        "published_at": post.get("date_gmt", "") + "Z" if post.get("date_gmt") else "",
                        "text": post.get("content", {}).get("rendered", ""),
                        "summary": post.get("excerpt", {}).get("rendered", ""), "tags": terms}
                add(post.get("link", ""), url, hint)
            if len(posts) < params["per_page"]:
                break
            if page == max_pages:
                diagnostics["limits"].append("wordpress_page_limit")

    # A current listing helps sources without feeds; it is refreshed on every run.
    if not audit.get("feeds") or archive and source["kind"] in ("html", "newsquest", "reach"):
        rec, data = client.get(source["home"], "html_listing")
        diagnostics["listing_pages"] += 1
        if rec.get("status") == 200:
            soup = BeautifulSoup(data, "lxml")
            for a in soup.select("a[href]"):
                url = urljoin(source["home"], a["href"])
                if article_url(url, source):
                    add(url, source["home"])

    if archive and source["id"] == "sky_sports":
        old_pages = 0
        for page in range(1, max_pages + 1):
            if STOP.is_set():
                break
            url = f"https://www.skysports.com/listing/sport/football/{page}"
            rec, data = client.get(url, "listing_json", ttl=3600)
            diagnostics["listing_pages"] += 1
            try:
                block = json.loads(data) if rec.get("status") == 200 else {}
            except ValueError:
                block = {}
            if not isinstance(block, dict) or not block.get("items"):
                diagnostics["routes_with_errors"].append({"url": url, "status": rec.get("status"), "error": rec.get("error") or "no_listing_items"})
                break
            soup = BeautifulSoup(block["items"], "lxml")
            times = [date(t.get("datetime")) for t in soup.select("time[datetime]")]
            times = [t for t in times if t]
            for a in soup.select("h3 a[href]"):
                target = urljoin(url, a["href"])
                if article_url(target, source):
                    add(target, url)
            old_pages = old_pages + 1 if times and max(times) < start else 0
            if old_pages >= 2 or not block.get("hasNext", True):
                break
            if page == max_pages:
                diagnostics["limits"].append("sky_listing_page_limit")

    if (archive or not found or not audit.get('feeds')) and not audit.get("apis"):
        # Sitemaps are discovery hints only: the article's original date is checked later.
        if source['id'] == 'goal':
            queue = [('https://www.goal.com/en/sitemap/google-news.xml', 0)]
            diagnostics['limits'].append('goal_dated_news_sitemap_only_deep_editorial_archive_not_exhausted')
        else:
            queue = [(u, 0) for u in audit.get("sitemaps", [])]
        visited = set()
        sitemap_budget = max_pages if archive else min(max_pages, 12)
        while queue and diagnostics["sitemap_pages"] < sitemap_budget:
            if STOP.is_set():
                diagnostics["limits"].append("interrupted")
                break
            url, depth = queue.pop(0)
            if url in visited or depth > 3:
                continue
            visited.add(url)
            if re.search(r"(?:video|image|people|person|author|category|tag|/online/|/ar/|/es/|/pt/|/de/|/fr/)", url, re.I):
                continue
            if source["id"] == "rotowire" and re.search(r"(?:nfl|nba|mlb|nhl|cfl|golf|nascar|tennis|cricket|college|mma|wnba|players|start-sit)", url, re.I):
                continue
            if source["id"] == "goal" and urlsplit(url).path not in ("/sitemap", "/sitemap.xml") and not urlsplit(url).path.startswith("/en/"):
                continue
            years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", url)
            if years and all(int(y) < start.year for y in years):
                continue
            rec, data = client.get(url, "sitemap", ttl=3600)
            diagnostics["sitemap_pages"] += 1
            if rec.get("status") != 200:
                diagnostics["routes_with_errors"].append({"url": url, "status": rec.get("status"), "error": rec.get("error")})
                continue
            try:
                tree = xml(data)
            except etree.Error:
                continue
            if etree.QName(tree).localname == "sitemapindex":
                children = []
                for node in tree:
                    loc = node.findtext("{*}loc")
                    last = date(node.findtext("{*}lastmod"))
                    month = re.search(r"map_art_(20\d\d)-(\d\d)-01", loc or "")
                    if month:
                        ym = (int(month.group(1)), int(month.group(2)))
                        if not (since_ym := (start.year, start.month)) <= ym <= (end.year, end.month):
                            continue
                    if loc and not (last and last < start - timedelta(days=2)):
                        children.append((loc, depth + 1))
                # Prefer news and current-year maps, but record limits if the budget is exhausted.
                children.sort(key=lambda x: ("news" not in x[0], str(start.year) not in x[0]))
                queue.extend(children)
            else:
                for node in tree:
                    loc = node.findtext("{*}loc")
                    if not loc:
                        continue
                    if loc.startswith("http://"):
                        loc = "https://" + loc[7:]
                    if not article_url(loc, source):
                        continue
                    pub = node.findtext(".//{*}publication_date")
                    last = date(node.findtext("{*}lastmod"))
                    original = date(pub)
                    if original and not start <= original < end:
                        continue
                    if not original and last and last < start - timedelta(days=2):
                        continue
                    # Dates in FA URLs bound discovery; original metadata still decides inclusion.
                    if source["id"] == "the_fa":
                        year = re.search(r"/news/(20\d\d)/", loc)
                        if year and int(year.group(1)) != start.year:
                            continue
                    add(loc, url, {"published_at": pub or "", "date_provenance": "news_sitemap.publication_date"})
        if queue:
            diagnostics["limits"].append("sitemap_page_or_depth_limit")
    return found, diagnostics


def load_records(directory):
    path = directory / "records.jsonl"
    if not path.exists():
        return {}
    with path.open() as stream:
        return {r["url"]: r for r in (json.loads(line) for line in stream)}


def write_csv(path, rows):
    temp = path.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in rows:
            row = {k: record.get(k, "") for k in FIELDS}
            for k in ("authors", "publisher", "source_tags", "clubs"):
                row[k] = json.dumps(row[k], ensure_ascii=False)
            writer.writerow(row)
    temp.replace(path)


def export(directory, records):
    rows = sorted(records.values(), key=lambda r: (r.get("published_at", ""), r["url"]))
    tmp = directory / "records.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(directory / "records.jsonl")
    groups = {name: [] for name in ("news", "posts", "partial", "review", "excluded")}
    for row in rows:
        if row.get("exclusion_reason"):
            group = "excluded"
        elif row.get("text_status") != "full_text":
            group = "partial"
        elif row.get("selection_status") != "included":
            group = "review"
        elif row.get("content_type") == "post":
            group = "posts"
        else:
            group = "news"
        groups[group].append(row)
    for name, group in groups.items():
        write_csv(directory / (name + ".csv"), group)
    return {name: len(group) for name, group in groups.items()}


@contextmanager
def source_lock(directory):
    with (directory / ".collect.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another collector is already running for this source")
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def collect(source_id, *, start=None, end=None, archive=False, max_pages=120, max_articles=None, refresh=False):
    started_at = utcnow()
    started_clock = time.monotonic()
    source = next(s for s in catalogue() if s["id"] == source_id)
    if source["scope"] != "news":
        raise ValueError("Source explicitly excluded by the user")
    directory = ROOT / "data" / source_id
    directory.mkdir(parents=True, exist_ok=True)
    with source_lock(directory):
        audit_file = directory / "audit.json"
        audit = json.loads(audit_file.read_text()) if audit_file.exists() else audit_one(source)
        if audit.get("status") in ("requires_social_api", "no_working_route"):
            raise RuntimeError(audit["reason"])
        records = load_records(directory)
        now = datetime.now(timezone.utc)
        # Overlap prevents losses at poll boundaries. Existing rows are preserved across windows.
        until = boundary(end, now)
        since = boundary(start, until - timedelta(days=3))
        if since >= until:
            raise ValueError("start must be earlier than exclusive end")
        aliases = json.loads((ROOT / "news_ingest" / "club_aliases.json").read_text())["aliases"]
        client = Client(directory)
        discovered, diagnostics = discover(source, audit, client, since, until, archive, max_pages)
        added = updated = skipped = 0
        errors = []
        attempted = 0
        # Prefer dates in window and known football URLs, without silently removing unknown dates.
        items = sorted(discovered.items(), key=lambda x: (not article_url(x[0], source), x[1].get("published_at", "")), reverse=False)
        for original_url, hint in items:
            if STOP.is_set():
                diagnostics["limits"].append("interrupted")
                break
            hint_date = date(hint.get("published_at"))
            if hint_date and not since <= hint_date < until:
                skipped += 1
                continue
            if original_url in records and not refresh:
                records[original_url]["last_seen_at"] = utcnow()
                skipped += 1
                continue
            if max_articles is not None and attempted >= max_articles:
                diagnostics["limits"].append("article_budget")
                break
            attempted += 1
            if source_id == 'rotowire' and '/soccer/article/' not in original_url:
                rec, body = {'status':None, 'error':'player_profile_is_not_a_news_article', 'checked_at':utcnow()}, b''
            else:
                rec, body = client.get(hint.get('fetch_url', original_url), "article", ttl=0 if refresh else 86400)
            if rec.get("status") != 200:
                errors.append({"url": original_url, "status": rec.get("status"), "error": rec.get("error")})
                if original_url in records and records[original_url].get('text_status') == 'full_text':
                    # A temporary refresh failure must not replace a good saved article.
                    errors[-1]['existing_record_preserved'] = True
                    skipped += 1
                    continue
                # Preserve feed evidence even if a page is temporarily unavailable or access-restricted.
                record = extract(b"", original_url, source_id, hint)
                record["text_status"] = "partial"
                record["extraction_method"] = "feed_summary_page_unavailable"
            else:
                record = extract(body, original_url, source_id, hint)
            if source_id == 'rotowire' and '/soccer/article/' not in original_url:
                record['exclusion_reason'] = 'player_profile_is_not_a_news_article'
            if not record["title"]:
                errors.append({"url": original_url, "error": "no_article_title"})
                continue
            published = date(record["published_at"])
            if published and not since <= published < until:
                skipped += 1
                continue
            status, reason, clubs = selection(record, aliases)
            if not published:
                status, reason = "review", "missing_or_timezone_ambiguous_publication_date"
            record.update(source=source["name"], source_id=source_id,
                          fetched_at=rec.get("checked_at", utcnow()), last_seen_at=utcnow(),
                          discovered_via=hint["discovered_via"], raw_path=rec.get("raw_path", ""),
                          selection_status=status, selection_reason=reason, clubs=clubs)
            previous = records.get(record["url"])
            record["first_seen_at"] = previous.get("first_seen_at", utcnow()) if previous else utcnow()
            if previous:
                updated += 1
                # Store revisions separately; they do not create duplicate article rows.
                if previous.get("content_hash") != record["content_hash"]:
                    with (directory / "revisions.jsonl").open("a") as revision:
                        revision.write(json.dumps(previous, ensure_ascii=False) + "\n")
            else:
                added += 1
            records[record["url"]] = record
            if attempted % 100 == 0:
                export(directory, records)
                print(json.dumps({"source": source_id, "processed": attempted, "discovered": len(discovered), "new": added}), flush=True)
        counts = export(directory, records)
        summary = {"source_id": source_id, "source": source["name"], "completed_at": utcnow(),
                   "started_at": started_at, "duration_seconds": round(time.monotonic() - started_clock, 3),
                   "window_start": since.isoformat(), "window_end_exclusive": until.isoformat(),
                   "mode": "backfill" if archive else "latest", "discovered": len(discovered),
                   "attempted_articles": attempted, "added": added, "updated": updated,
                   "skipped": skipped, "counts_all_saved_windows": counts,
                   "diagnostics": diagnostics, "article_errors": errors,
                   "full_site_coverage_asserted": False}
        atomic_json(directory / "last_run.json", summary)
        with (directory / "runs.jsonl").open("a") as out:
            out.write(json.dumps(summary, ensure_ascii=False) + "\n")
        valid_full = [r for r in records.values() if r.get("text_status") == "full_text" and not r.get("exclusion_reason")]
        audit["collector_verified"] = bool(valid_full)
        audit["saved_counts"] = counts
        audit["last_collection_at"] = summary["completed_at"]
        audit["status"] = "collector_verified" if valid_full else "partial_only" if records else "no_articles_extracted"
        audit["reason"] = "Validated article extraction; archive coverage is not asserted" if valid_full else "See last_run.json for article/date/language/extraction limitations"
        atomic_json(audit_file, audit)
        print(json.dumps({"source": source_id, "new": added, "updated": updated, "counts": counts, "status": audit["status"]}, ensure_ascii=False), flush=True)
        return summary


def run(source_id, argv=None):
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    parser = argparse.ArgumentParser(description=f"Incremental news collector: {source_id}. Dates use Moscow time; --end is exclusive.")
    parser.add_argument("--start", help="ISO date/time, e.g. 2026-10-26")
    parser.add_argument("--end", help="Exclusive ISO date/time, e.g. 2026-11-09")
    parser.add_argument("--archive", action="store_true", help="Also inspect historical API/listings/sitemaps")
    parser.add_argument("--max-pages", type=int, default=120, help="Per-route page budget; any truncation is logged")
    parser.add_argument("--max-articles", type=int, help="Optional explicit article budget; truncation is logged")
    parser.add_argument("--refresh-existing", action="store_true", help="Re-fetch existing URLs and preserve prior text revisions")
    args = parser.parse_args(argv)
    return collect(source_id, start=args.start, end=args.end, archive=args.archive,
                   max_pages=args.max_pages, max_articles=args.max_articles, refresh=args.refresh_existing)
