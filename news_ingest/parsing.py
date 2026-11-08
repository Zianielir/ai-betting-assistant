"""Article extraction and explicit, reviewable EPL selection rules."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

from bs4 import BeautifulSoup


def clean(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n[ \t]+", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()


def canonical(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")
             and k.lower() not in ("fbclid", "gclid", "ref", "output", "amp")]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(query), ""))


def date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    if not dt.tzinfo:
        return None  # Never silently invent a timezone.
    return dt.astimezone(timezone.utc)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def types(node):
    t = node.get("@type", [])
    return set(t if isinstance(t, list) else [t])


def names(value):
    if isinstance(value, str):
        if value.strip().startswith("["):
            try:
                return names(json.loads(value))
            except ValueError:
                pass
        return [clean(x) for x in value.split(",") if clean(x)]
    if isinstance(value, list):
        return [n for item in value for n in names(item)]
    if isinstance(value, dict):
        return names(value.get("name", ""))
    return []


def extract(body, url, source_id, hint=None):
    hint = hint or {}
    soup = BeautifulSoup(body, "lxml")
    objects = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            objects.extend(walk(json.loads(script.string or script.get_text())))
        except (ValueError, TypeError):
            pass
    article_objects = [o for o in objects if types(o) & {"NewsArticle", "Article", "BlogPosting", "LiveBlogPosting"}]
    obj = max(article_objects, key=lambda o: len(str(o.get("articleBody", ""))), default={})
    meta = {}
    for tag in soup.select("meta[content]"):
        key = str(tag.get("property") or tag.get("name") or "").lower()
        meta.setdefault(key, []).append(tag["content"])

    def first(*keys):
        return next((meta[k][0] for k in keys if meta.get(k)), "")

    title = clean(obj.get("headline") or (soup.h1.get_text(" ", strip=True) if soup.h1 else "")
                  or first("og:title") or hint.get("title", ""))
    can = soup.select_one('link[rel="canonical"][href]')
    target = canonical(urljoin(url, can["href"]) if can else url)
    published_raw = obj.get("datePublished") or first("article:published_time", "date", "dc.date.issued")
    provenance = "jsonld.datePublished" if obj.get("datePublished") else "article_metadata"
    if not published_raw and hint.get("published_at"):
        published_raw, provenance = hint["published_at"], hint.get('date_provenance', "feed_or_api_publication")
    published = date(published_raw)
    display_date = ""
    if not published:
        time_node = soup.find("time", datetime=True)
        if time_node:
            display_date = clean(time_node.get("datetime", ""))
    modified = date(obj.get("dateModified") or first("article:modified_time"))
    tags = list(hint.get("tags", []))
    tags.extend(meta.get("article:tag", []))
    tags.extend(meta.get("parsely-tags", []))
    # Sports Mole's meta keywords are site-wide, not article-level tags.
    if source_id != "sports_mole":
        tags.extend(names(obj.get("keywords")))
        tags.extend(names(first("news_keywords", "keywords")))
    tags.extend(names(obj.get("articleSection") or obj.get("ArticleSection")))
    for node in walk(obj.get("mentions", [])):
        if types(node) & {"SportsTeam", "Person", "SportsOrganization"}:
            tags.extend(names(node))
    language = (soup.html.get("lang", "") if soup.html else "") or obj.get("inLanguage", "") or hint.get("language", "")
    kind = "article"
    reason = ""
    if any("LiveBlogPosting" in types(o) for o in article_objects) or re.search(r"\blive[- ](?:blog|updates|score)\b", title, re.I):
        reason = "live_blog"
    elif re.match(r"^video\s*:", title, re.I) or "/video/" in url or "/videos/" in url or (not article_objects and any("VideoObject" in types(o) for o in objects)):
        reason = "video"
    elif language and not str(language).lower().startswith("en"):
        reason = "non_english"
    accessible = obj.get("isAccessibleForFree", True)
    paywalled = accessible is False or str(accessible).lower() == "false"
    paragraphs = []
    method = ""
    if obj.get("articleBody") and not paywalled:
        raw = str(obj["articleBody"])
        parsed = BeautifulSoup(raw, "lxml")
        paragraphs = [p.get_text(" ", strip=True) for p in parsed.find_all("p")] or raw.splitlines()
        method = "jsonld.articleBody"
    if not paragraphs and not paywalled:
        selectors = {
            "sky_sports": ['.sdc-article-body .sdc-article-body__paragraph'],
            "teamtalk": ['.article-body p', '.article__body p', '.article-content p'],
            "espn": ['.article-body p', '.story__content p'],
            "independent": ['[id="main"] article p', '.body-content p'],
            "sports_mole": ['#article_body p', '.article_body p', '[itemprop="articleBody"] p'],
            "premier_league": ['.article-content p', '.article__body p'],
            "the_fa": ['.article-body p', '.article__body p', '.article-content p'],
        }
        choices = selectors.get(source_id, []) + ['[itemprop="articleBody"] p', '.article-body p',
                   '.entry-content p', '.article__body p', '.article-body-wrapper p',
                   '.article-body-content p', '.story-body p', 'article p']
        for selector in choices:
            selected = soup.select(selector)
            values = []
            for p in selected:
                if p.find_parent(["nav", "aside", "footer"]):
                    continue
                parent = p.parent
                bad = False
                for _ in range(4):
                    if not parent:
                        break
                    attrs = " ".join(parent.get("class", [])) + " " + str(parent.get("id", ""))
                    if re.search(r"comment|recommend|related|newsletter|paywall", attrs, re.I):
                        bad = True
                        break
                    parent = parent.parent
                if not bad:
                    values.append(p.get_text(" ", strip=True))
            if sum(len(v) for v in values) > 180:
                paragraphs, method = values, "html:" + selector
                break
    if not paragraphs and hint.get("text") and not paywalled:
        paragraphs = [BeautifulSoup(hint["text"], "lxml").get_text(" ", strip=True)]
        method = "feed_or_api_content"
    bad_paragraph = re.compile(r"^(?:Please use Chrome|Also See:|Read more:|READ MORE|DON.T MISS|Watch Sky Sports|Watch FREE |Stream Sky Sports|Get Sky Sports|Play Super 6|Not got Sky\?|Got Sky\?|AccaFreeze|Sign up (?:for|to)|Subscribe (?:to|now)|Follow us on|READ NEXT|Join our|We use cookies|Removed from bookmarks|Want to bookmark|Start your Independent|I would like to be emailed|Read our Privacy|Already subscribed|Please refresh the page|Join thought-provoking)", re.I)
    paras = []
    for p in paragraphs:
        p = clean(p)
        if p and not bad_paragraph.match(p) and p not in paras:
            paras.append(p)
    text = clean("\n\n".join(paras))
    if len(text) < 180:
        # An excerpt is preserved but is never silently called full text.
        text = clean(BeautifulSoup(hint.get("summary", "") or first("og:description", "description"), "lxml").get_text(" ", strip=True))
        method = "summary_only"
    quality = "full_text" if len(text) >= 180 and method != "summary_only" and not paywalled else "partial"
    if paywalled:
        method = "paywall_summary_only"
    if hint.get("content_type") == "post":
        kind = "post"
    if not reason and not article_objects and not soup.select_one('article, [itemprop="articleBody"], .article-body, .article__body, .entry-content') and not published_raw:
        reason = 'non_article_page'
    return {
        "document_id": hashlib.sha256(target.encode()).hexdigest()[:24],
        "url": target, "title": title, "text": text,
        "published_at": published.isoformat() if published else "",
        "published_date_display": display_date, "date_precision": "timestamp" if published else "display_only" if display_date else "unknown",
        "published_at_raw": str(published_raw or ""), "date_provenance": provenance,
        "modified_at": modified.isoformat() if modified else "",
        "authors": names(obj.get("author")) or names(first("author")),
        "publisher": names(obj.get('publisher')),
        "source_tags": list(dict.fromkeys(clean(t) for t in tags if clean(t))),
        "language": str(language or "unknown"), "content_type": kind,
        "text_status": quality, "extraction_method": method,
        "exclusion_reason": reason,
        "word_count": len(text.split()), "content_hash": hashlib.sha256(text.encode()).hexdigest(),
    }


def selection(record, aliases):
    tag_text = " | ".join(record.get("source_tags", []))
    lead = record.get("title", "") + "\n" + record.get("text", "")[:1200]
    clubs = []
    tag_clubs = []
    for club, variants in aliases.items():
        pattern = r"(?<!\w)(?:" + "|".join(re.escape(x) for x in variants) + r")(?!\w)"
        if re.search(pattern, tag_text, re.I):
            tag_clubs.append(club)
        if re.search(pattern, lead, re.I):
            clubs.append(club)
    all_clubs = sorted(set(clubs + tag_clubs))
    if re.search(r"\b(?:women(?:'s|s)?|under[- ]?\d\d|u[- ]?\d\d|youth|academy)\b", record.get("title", "") + ' ' + tag_text + ' ' + record.get('url', '') + ' ' + record.get('text', '')[:500], re.I):
        return "review", "possible_women_or_youth_team", all_clubs
    if re.search(r"\b(?:premier league|english premier league|epl)\b", tag_text, re.I):
        return "included", "native_league_tag", all_clubs
    if tag_clubs:
        return "included", "native_club_tag", all_clubs
    if re.search(r"\b(?:premier league|english premier league|epl)\b", lead, re.I):
        return "included", "title_or_lead_league", all_clubs
    if clubs:
        title_clubs = any(re.search(r"(?<!\w)(?:" + "|".join(re.escape(v) for v in aliases[club]) + r")(?!\w)", record.get("title", ""), re.I) for club in clubs)
        if title_clubs:
            return "included", "title_club", all_clubs
        return "review", "lead_club_mention_needs_relevance_review", all_clubs
    # Person-only stories need a verified player-club relationship: do not invent it.
    return "review", "no_verified_epl_evidence", []
