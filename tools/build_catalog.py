"""Seed a traceable source catalogue from the exact user workbook (no network)."""
import json
from pathlib import Path
import shutil

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT.parent / "Istochniki_APL.xlsx"
TARGET = ROOT / "research" / "Istochniki_APL.xlsx"
TARGET.parent.mkdir(exist_ok=True)
if not TARGET.exists():
    shutil.copy2(INPUT, TARGET)

# URLs are candidates until audit.py tests them; no workbook claims become observations.
PROFILES = [
 ("sky_sports", "https://www.skysports.com/football/news", ["https://www.skysports.com/rss/11095"], [], ["https://www.skysports.com/sitemap.xml"], "sky"),
 ("espn", "https://www.espn.com/soccer/", ["https://www.espn.com/espn/rss/soccer/news"], [], [], "html"),
 ("talksport", "https://talksport.com/football/", ["https://talksport.com/football/feed/"], [], [], "wordpress"),
 ("independent", "https://www.independent.co.uk/sport/football", ["https://www.independent.co.uk/sport/football/premier-league/rss", "https://www.independent.co.uk/sport/football/rss"], [], ["https://www.independent.co.uk/sitemap.xml"], "html"),
 ("fantasy_football_scout", "https://www.fantasyfootballscout.co.uk/", ["https://www.fantasyfootballscout.co.uk/feed/"], ["https://www.fantasyfootballscout.co.uk/wp-json/wp/v2/posts"], ["https://www.fantasyfootballscout.co.uk/post-sitemap.xml"], "wordpress"),
 ("rotowire", "https://www.rotowire.com/soccer/articles/", ["https://www.rotowire.com/rss/articles.php?sport=Soccer", "https://www.rotowire.com/rss/news.php?sport=Soccer"], [], [], "html"),
 ("sports_mole", "https://www.sportsmole.co.uk/football/premier-league/", ["https://www.sportsmole.co.uk/rss/football.xml"], [], ["https://www.sportsmole.co.uk/sitemap.xml"], "html"),
 ("fpl", "https://fantasy.premierleague.com/", [], [], [], "excluded"),
 ("official_clubs", "", [], [], [], "excluded"),
 ("football_london", "https://www.football.london/", ["https://www.football.london/?service=rss", "https://www.football.london/chelsea-fc/?service=rss"], [], [], "reach"),
 ("manchester_evening_news", "https://www.manchestereveningnews.co.uk/sport/football/", ["https://www.manchestereveningnews.co.uk/sport/football/?service=rss", "https://www.manchestereveningnews.co.uk/all-about/manchester-united-fc?service=rss"], [], [], "reach"),
 ("liverpool_echo", "https://www.liverpoolecho.co.uk/sport/football/", ["https://www.liverpoolecho.co.uk/sport/football/?service=rss", "https://www.liverpoolecho.co.uk/all-about/liverpool-fc?service=rss"], [], [], "reach"),
 ("chronicle_live", "https://www.chroniclelive.co.uk/sport/football/", ["https://www.chroniclelive.co.uk/sport/football/?service=rss", "https://www.chroniclelive.co.uk/all-about/newcastle-united-fc?service=rss"], [], [], "reach"),
 ("bournemouth_echo", "https://www.bournemouthecho.co.uk/sport/cherries/", ["https://www.bournemouthecho.co.uk/sport/cherries/clubnews/rss/", "https://www.bournemouthecho.co.uk/sport/cherries/rss/", "https://www.bournemouthecho.co.uk/sport/rss/"], [], [], "newsquest"),
 ("the_argus", "https://www.theargus.co.uk/sport/albion/", ["https://www.theargus.co.uk/sport/albion/rss/", "https://www.theargus.co.uk/sport/rss/"], [], [], "newsquest"),
 ("west_london_sport", "https://www.westlondonsport.com/", ["https://www.westlondonsport.com/feed"], ["https://www.westlondonsport.com/wp-json/wp/v2/posts"], ["https://www.westlondonsport.com/post-sitemap.xml"], "wordpress"),
 ("fotmob", "https://www.fotmob.com/news", ["https://www.fotmob.com/rss/news"], [], [], "html"),
 ("premier_league", "https://www.premierleague.com/en/news", [], [], ["https://www.premierleague.com/sitemap.xml"], "html"),
 ("the_fa", "https://www.thefa.com/news", [], [], ["https://www.thefa.com/sitemap.xml"], "html"),
 ("livescore", "https://www.livescore.com/en/news/", [], [], ["https://www.livescore.com/sitemap.xml"], "html"),
 ("goal", "https://www.goal.com/en/news", [], [], ["https://www.goal.com/en/sitemap/google-news.xml"], "html"),
 ("fourfourtwo", "https://www.fourfourtwo.com/news", ["https://www.fourfourtwo.com/feeds.xml"], [], ["https://www.fourfourtwo.com/sitemap.xml"], "html"),
 ("mirror", "https://www.mirror.co.uk/sport/football/", ["https://www.mirror.co.uk/sport/football/?service=rss"], [], [], "reach"),
 ("teamtalk", "https://www.teamtalk.com/", ["https://www.teamtalk.com/feed", "https://www.teamtalk.com/rss"], [], ["https://www.teamtalk.com/teamtalk-post-sitemap0.xml"], "teamtalk"),
 ("football_insider", "https://www.footballinsider247.com/", ["https://www.footballinsider247.com/feed/"], ["https://www.footballinsider247.com/wp-json/wp/v2/posts"], ["https://www.footballinsider247.com/post-sitemap.xml"], "wordpress"),
 ("caughtoffside", "https://www.caughtoffside.com/", ["https://www.caughtoffside.com/feed/"], ["https://www.caughtoffside.com/wp-json/wp/v2/posts"], ["https://www.caughtoffside.com/post-sitemap.xml"], "wordpress"),
 ("gianluca_di_marzio", "https://www.gianlucadimarzio.com/", ["https://www.gianlucadimarzio.com/rss/"], [], ["https://www.gianlucadimarzio.com/sitemap.xml"], "html"),
 ("fabrizio_romano", "https://x.com/FabrizioRomano", [], [], [], "social"),
 ("david_ornstein", "https://www.nytimes.com/athletic/author/david-ornstein/", ["https://www.nytimes.com/athletic/rss/author/david-ornstein/"], [], [], "author_feed"),
 ("premier_injuries", "https://www.premierinjuries.com/injury-table.php", [], [], [], "excluded"),
 ("squawka", "https://www.squawka.com/en/news/", ["https://www.squawka.com/en/feed/"], [], ["https://www.squawka.com/sitemap.xml"], "html"),
 ("api_football", "https://www.api-football.com/", [], [], [], "excluded"),
 ("sportmonks", "https://www.sportmonks.com/", [], [], [], "excluded"),
 ("football_data_org", "https://www.football-data.org/", [], [], [], "excluded"),
]

rows = list(openpyxl.load_workbook(TARGET, read_only=True, data_only=True).active.values)
assert len(rows) - 1 == len(PROFILES) == 34
catalog = []
for row_number, (row, profile) in enumerate(zip(rows[1:], PROFILES), start=2):
    key, home, feeds, api, sitemaps, kind = profile
    catalog.append({"id": key, "name": row[0], "workbook_row": row_number,
                    "workbook_claims": dict(zip(rows[0], row)), "home": home,
                    "feed_candidates": feeds, "api_candidates": api,
                    "sitemap_candidates": sitemaps, "kind": kind,
                    "scope": "excluded" if kind == "excluded" else "news",
                    "exclusion_reason": ("official_club_sites_excluded_by_user" if key == "official_clubs"
                                         else "structured_data_excluded_by_user") if kind == "excluded" else "",
                    "api_note": "No documented public news API verified; audit records endpoint evidence separately."})
(ROOT / "news_ingest" / "sources.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
print(f"Catalogue: {len(catalog)} rows; {sum(s['scope']=='news' for s in catalog)} news candidates")
