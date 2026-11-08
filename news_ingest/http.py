"""Polite HTTP with robots checks, conditional requests, retries and local evidence."""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

AGENT = "APLNewsResearch"
USER_AGENT = AGENT + "/1.0 (+https://github.com/Zianielir/ai-betting-assistant)"
_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class Client:
    def __init__(self, directory: Path, delay=1.0):
        self.directory = directory
        self.cache = directory / "raw"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.events = directory / "http_events.jsonl"
        self.delay = delay
        self.robots = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})

    def _event(self, record):
        with self.events.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    def allowed(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            rec, body = self.get(origin + "/robots.txt", "robots", check_robots=False, ttl=86400)
            parser = RobotFileParser(origin + "/robots.txt")
            if rec.get("status") == 200:
                text = body.decode("utf-8", errors="replace")
                parser.parse(text.splitlines())
                self.robots[origin] = (parser, text)
            elif rec.get("status") in (404, 410):
                parser.parse([])
                self.robots[origin] = (parser, "")
            else:
                self.robots[origin] = (None, "")
        parser, _ = self.robots[origin]
        if parser is None:
            return False, "robots_unavailable"
        return parser.can_fetch(AGENT, url), "robots_disallow"

    def get(self, url, purpose="article", *, check_robots=True, ttl=0, redirects=0):
        if urlsplit(url).scheme not in ("http", "https"):
            return {"url": url, "status": None, "error": "unsupported_url"}, b""
        key = hashlib.sha256(url.encode()).hexdigest()
        meta_path = self.cache / (key + ".json")
        body_path = self.cache / (key + ".gz")
        cached = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if check_robots:
            permitted, reason = self.allowed(url)
            if not permitted:
                rec = {"url": url, "checked_at": utcnow(), "purpose": purpose,
                       "status": None, "error": reason, "attempts": 0}
                self._event(rec)
                return rec, b""
        if body_path.exists():
            try:
                gzip.decompress(body_path.read_bytes())
            except (OSError, EOFError):
                body_path.unlink()  # Recover a cache write interrupted by a process stop.
        if (cached.get("status") == 200 and body_path.exists() and ttl > 0
                and time.time() - body_path.stat().st_mtime < ttl):
            return {**cached, "cache_hit": True}, gzip.decompress(body_path.read_bytes())
        headers = {}
        if cached.get("status") == 200 and body_path.exists():
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        host = urlsplit(url).netloc
        with _guard:
            lock = _locks.setdefault(host, threading.Lock())
        record = {"url": url, "purpose": purpose, "checked_at": utcnow()}
        data = b""
        with lock:
            for attempt in range(1, 4):
                time.sleep(max(0, self.delay - (time.monotonic() - _last.get(host, 0))))
                record.update(attempts=attempt, error="")
                try:
                    with self.session.get(url, timeout=(7, 22), headers=headers,
                                          allow_redirects=False, stream=True) as response:
                        record.update(status=response.status_code,
                                      content_type=response.headers.get("Content-Type", ""),
                                      etag=response.headers.get("ETag", ""),
                                      last_modified=response.headers.get("Last-Modified", ""))
                        if response.status_code == 304 and body_path.exists():
                            record.update(status=200, not_modified=True)
                            data = gzip.decompress(body_path.read_bytes())
                        elif response.is_redirect:
                            record["redirect_to"] = urljoin(url, response.headers.get("Location", ""))
                        else:
                            chunks = []
                            length = 0
                            for chunk in response.iter_content(65536):
                                length += len(chunk)
                                if length > 20_000_000:
                                    raise ValueError("response_size_limit")
                                chunks.append(chunk)
                            data = b"".join(chunks)
                        retry_after = response.headers.get("Retry-After", "")
                except (requests.RequestException, ValueError) as exc:
                    record.update(status=None, error=type(exc).__name__)
                    retry_after = ""
                _last[host] = time.monotonic()
                self._event(dict(record))
                retry = record.get("status") in (None, 408, 429, 500, 502, 503, 504)
                if not retry or attempt == 3:
                    break
                pause = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                if pause > 60:
                    record["error"] = "retry_after_exceeds_run_budget"
                    break
                time.sleep(pause)
        if record.get("redirect_to"):
            if redirects >= 5:
                return {**record, "error": "redirect_limit"}, b""
            target, body = self.get(record["redirect_to"], purpose, check_robots=check_robots,
                                    ttl=ttl, redirects=redirects + 1)
            return {**target, "requested_url": url}, body
        record.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if record.get("status") == 200:
            temporary_body = body_path.with_suffix(".gz.tmp")
            temporary_body.write_bytes(gzip.compress(data))
            temporary_body.replace(body_path)
            record["raw_path"] = str(body_path.relative_to(self.directory))
        atomic_json(meta_path, record)
        return record, data
