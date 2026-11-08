import csv
from pathlib import Path
import tempfile
import unittest

from news_ingest.collector import export, load_records, source_lock
from news_ingest.parsing import canonical, date, extract, selection, names


class CollectionTests(unittest.TestCase):
    def test_category_page_is_not_an_article(self):
        row = extract(b'<html lang="en"><h1>Arsenal news</h1><nav>Latest stories</nav></html>', 'https://example.org/teams/arsenal', 'example')
        self.assertEqual(row['exclusion_reason'], 'non_article_page')

    def test_display_only_date_does_not_invent_publication_timestamp(self):
        row = extract(b'<html lang="en"><article><h1>Arsenal update</h1><time datetime="08 Nov 2026">08 Nov 2026</time></article></html>', 'https://example.org/news/1', 'example')
        self.assertEqual(row['published_at'], '')
        self.assertEqual(row['published_date_display'], '08 Nov 2026')
        self.assertEqual(row['date_precision'], 'display_only')

    def test_json_encoded_native_tags_are_preserved_as_names(self):
        self.assertEqual(names('["Arsenal", "Champions League"]'), ['Arsenal', 'Champions League'])

    def test_women_team_and_incidental_historical_mentions_require_review(self):
        aliases = {'Arsenal':['Arsenal'], 'Man Utd':['Manchester United']}
        row = {'title':'Arsenal win', 'text':'Arsenal women beat Benfica', 'source_tags':['Arsenal']}
        self.assertEqual(selection(row, aliases)[0], 'review')
        row = {'title':'Ronaldo goal tracker', 'text':'He previously played for Manchester United.', 'source_tags':[]}
        self.assertEqual(selection(row, aliases)[0], 'review')
    def test_publication_date_never_falls_back_to_updated_or_local_timezone(self):
        self.assertIsNone(date("2026-11-08T12:00:00"))
        row = extract(b'<html lang="en"><h1>Arsenal news</h1><meta property="article:modified_time" content="2026-11-08T12:00:00Z"></html>',
                      "https://example.org/news/1", "example")
        self.assertEqual(row["published_at"], "")
        self.assertTrue(row["modified_at"])

    def test_live_blog_in_schema_graph_is_excluded(self):
        page = b'''<html lang="en"><h1>Match updates</h1><script type="application/ld+json">
        {"@graph":[{"@type":"NewsArticle","datePublished":"2026-11-08T12:00:00Z"},
        {"@type":"LiveBlogPosting","headline":"Match updates"}]}</script></html>'''
        self.assertEqual(extract(page, "https://example.org/1", "example")["exclusion_reason"], "live_blog")

    def test_sitewide_keywords_are_not_sports_mole_article_tags(self):
        row = extract(b'<html lang="en"><h1>Example</h1><meta name="keywords" content="Arsenal,F1,rugby"></html>',
                      "https://example.org/1", "sports_mole")
        self.assertEqual(row["source_tags"], [])

    def test_generic_united_city_does_not_trigger_club_match(self):
        row = {"title": "United and City prepare", "text": "A report", "source_tags": []}
        status, _, clubs = selection(row, {"Man Utd": ["Manchester United"], "Man City": ["Manchester City"]})
        self.assertEqual(status, "review")
        self.assertEqual(clubs, [])

    def test_canonical_tracking_query_is_removed_but_article_id_preserved(self):
        self.assertEqual(canonical("https://Example.org/news/?id=7&utm_source=rss#body"),
                         "https://example.org/news?id=7")

    def test_upsert_preserves_csv_multiline_and_no_duplicate_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            row = {"url": "https://example.org/1", "title": 'Title, "quote"', "text": "First, paragraph\n\nВторой абзац",
                   "published_at": "2026-11-08T12:00:00+00:00", "source_tags": ["Arsenal"],
                   "authors": [], "clubs": ["Arsenal"], "text_status": "full_text", "selection_status": "included"}
            export(path, {row["url"]: row})
            records = load_records(path)
            records[row["url"]] = {**row, "title": "Revised title"}
            counts = export(path, records)
            self.assertEqual(counts["news"], 1)
            with (path / "news.csv").open(encoding="utf-8-sig", newline="") as f:
                saved = list(csv.DictReader(f))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["text"], row["text"])
            self.assertEqual(saved[0]["title"], "Revised title")

    def test_source_lock_prevents_overlapping_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            with source_lock(Path(directory)):
                with self.assertRaises(RuntimeError):
                    with source_lock(Path(directory)):
                        pass


if __name__ == "__main__":
    unittest.main()
