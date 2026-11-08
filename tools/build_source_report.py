"""Build the source audit workbook and an executable source-description notebook."""
from pathlib import Path
import csv
import json
import sys

import nbformat as nbf
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from news_ingest.audit import catalogue
from news_ingest.collector import FIELDS, load_records

EDA = ROOT / "EDA"
EDA.mkdir(exist_ok=True)
status_ru = {
    "excluded_by_user": "Исключён по решению пользователя",
    "no_working_route": "Рабочий маршрут не найден",
    "requires_social_api": "Нужен доступ к API соцсети",
    "routes_found": "Маршруты есть, сборщик не проверен",
    "collector_verified": "Извлечение статей проверено",
    "partial_only": "Только неполные или неподходящие материалы",
    "no_articles_extracted": "Статьи за окно не извлечены",
    "non_english_only": "Только материалы на другом языке",
    "feed_only_no_articles": "Лента есть, ссылки ведут на профили игроков",
    "partial_collector_verified": "Проверен сбор дат, ссылок и анонсов; полного текста нет",
}
def readable_reason(audit):
    status= audit['status']
    if status=='excluded_by_user':
        return 'Официальные сайты клубов исключены по решению пользователя.' if audit['id']=='official_clubs' else 'Источник структурированных данных исключён: сейчас собираем только статьи.'
    if status=='collector_verified':
        return 'Извлечение статей проверено. Полнота архива не установлена; см. ограничения и реальные даты сохранённых материалов.'
    if status=='non_english_only':
        return 'Найдены итальянские статьи; проверенные английские /en/ и /en/rss/ вернули 404.'
    if status=='partial_collector_verified':
        return 'Работает авторский RSS со ссылками, датами и анонсами. Полные тексты не получены; результат в partial.csv.'
    if audit['id']=='talksport' and status=='no_working_route':
        return 'robots.txt запрещает обращения сборщика к проверенным маршрутам. Это ограничение доступа, а не временный таймаут.'
    if audit['id']=='squawka' and status=='no_working_route':
        return 'Проверенные HTML и RSS маршруты вернули HTTP 403; рабочий разрешённый способ не подтверждён.'
    if audit['id']=='fabrizio_romano' and status=='requires_social_api':
        return 'Рабочий доступ к постам X не подтверждён. Альтернативная рассылка доступна, но проверенные материалы принадлежат другим авторам.'
    return audit.get('reason','')

def readable_limits(values):
    mapping={'goal_dated_news_sitemap_only_deep_editorial_archive_not_exhausted':'GOAL: используется датированная новостная карта; полный редакционный архив не исчерпан',
             'sky_listing_page_limit':'Достигнут бюджет страниц архива Sky',
             'sitemap_page_or_depth_limit':'Достигнут бюджет карт сайта или глубины обхода',
             'wordpress_page_limit':'Достигнут бюджет страниц API',
             'article_budget':'Достигнут явно заданный бюджет статей',
             'interrupted':'Сбор был остановлен с сохранением данных',
             'interrupted_by_user':'Сбор был остановлен с сохранением данных'}
    return '; '.join(mapping.get(v,v) for v in values)
summary, routes, runs, verification = [], [], [], []
for source in catalogue():
    directory = ROOT / "data" / source["id"]
    audit = json.loads((directory / "audit.json").read_text())
    counts = audit.get("saved_counts", {})
    verify_file = directory / "verification.json"
    check = json.loads(verify_file.read_text()) if verify_file.exists() else {}
    if check:
        verification.append(check)
    history = [json.loads(line) for line in (directory / "runs.jsonl").read_text().splitlines()] if (directory / "runs.jsonl").exists() else []
    archive_runs = [r for r in history if r["mode"] == "backfill"]
    archive_run = archive_runs[-1] if archive_runs else {}
    limits = archive_run.get("diagnostics", {}).get("limits", [])
    records = load_records(directory)
    row = {
        "source_id": source["id"], "source": source["name"], "workbook_row": source["workbook_row"],
        "status": status_ru.get(audit["status"], audit["status"]), "reason": readable_reason(audit),
        "rss": "\n".join(audit.get("feeds", [])), "api": "\n".join(audit.get("apis", []) + audit.get('other_news_apis', [])) or "Не подтверждён в проведённой проверке",
        "other_methods": audit.get('other_methods', ''),
        "html": "Проверено извлечение текста" if audit.get("collector_verified") else "Не подтверждено",
        "archive": "\n".join(audit.get("sitemaps", [])),
        "script": str((directory / "collect.py").relative_to(ROOT)) if (directory / "collect.py").exists() else "",
        "rerun_passed": bool(check.get("passed")), "news": counts.get("news", 0),
        "collector_mode": audit.get('entrypoint_mode', ''),
        "partial": counts.get("partial", 0), "review": counts.get("review", 0),
        "excluded": counts.get("excluded", 0), "posts": counts.get("posts", 0),
        "archive_limits": readable_limits(limits), "coverage": "Не проверялось по решению пользователя" if source['scope']=='excluded' else "Полнота сайта не доказана",
        "checked_at": audit["checked_at"], "api_note": audit.get("api_note", ""),
        "last_collection_error": str(audit.get('last_collection_error', '')),
        "saved_publication_min": min((r.get('published_at') for r in records.values() if r.get('published_at')), default=''),
        "saved_publication_max": max((r.get('published_at') for r in records.values() if r.get('published_at')), default=''),
    }
    summary.append(row)
    for route in audit.get("routes", []):
        routes.append({"source": source["name"], "kind": route["kind"], "url": route["url"],
                       "status": route.get("status"), "error": route.get("error", ""),
                       "attempts": route.get("attempts", 0), "entries": route.get("entries", ""),
                       "validation": route.get("validation", ""),
                       "checked_at": route.get("checked_at", "")})
    for run in history:
        runs.append({"source": source["name"], "completed_at": run["completed_at"], "mode": run["mode"],
                     "start": run["window_start"], "end_exclusive": run["window_end_exclusive"],
                     "discovered": run["discovered"], "attempted": run["attempted_articles"],
                     "new": run["added"], "updated": run["updated"],
                     "limits": ", ".join(run["diagnostics"]["limits"]), "errors": len(run["article_errors"]),
                     "duration_seconds": run.get('duration_seconds', '')})
    # Share metadata independently of the locally stored copyrighted full texts.
    columns = [c for c in FIELDS if c not in ("text", "raw_path")]
    with (directory / "metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, columns, extrasaction="ignore")
        writer.writeheader()
        for record in records.values():
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in record.items()})

with (EDA / "source_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
    writer = csv.DictWriter(handle, list(summary[0]))
    writer.writeheader()
    writer.writerows(summary)
(EDA / "source_routes.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2) + "\n")

wb = openpyxl.Workbook()
wb.remove(wb.active)
def sheet(name, records):
    ws = wb.create_sheet(name)
    if not records:
        ws.append(["Нет записей"])
        return
    columns = list(records[0])
    labels={'source_id':'ID источника','source':'Источник','workbook_row':'Строка исходного Excel','status':'Результат','reason':'Причина / ограничение','rss':'Рабочие RSS / Atom','api':'Подтверждённые API','html':'Извлечение HTML','archive':'Архивные маршруты','script':'Скрипт .py','rerun_passed':'Повторный запуск пройден','news':'Статьи АПЛ','partial':'Неполные тексты','review':'На проверку','excluded':'Исключённые','posts':'Короткие посты','archive_limits':'Ограничения архива','coverage':'Полнота охвата','checked_at':'Время проверки UTC','api_note':'Примечание об API','other_methods':'Другие способы','collector_mode':'Режим сборщика','saved_publication_min':'Первая сохранённая публикация UTC','saved_publication_max':'Последняя сохранённая публикация UTC','url':'Проверенный URL','kind':'Тип маршрута','error':'Ошибка','attempts':'Попыток','entries':'Элементов в ответе','validation':'Проверка принадлежности / содержания'}
    ws.append([labels.get(c,c) for c in columns])
    for record in records:
        ws.append([json.dumps(record.get(c), ensure_ascii=False) if isinstance(record.get(c), (dict, list)) else record.get(c, "") for c in columns])
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.row_dimensions[1].height = 32
    for i, column in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = 58 if column in ("reason", "url", "rss", "archive", "api_note") else 26
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if isinstance(cell.value,str) and cell.value.startswith(('https://','http://')) and '\n' not in cell.value:
                cell.hyperlink=cell.value
                cell.font=Font(color='0563C1',underline='single')
            if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "@")):
                cell.value = "'" + cell.value
        ws.row_dimensions[row[0].row].height = 60
sheet("Источники", summary)
sheet("Способы доступа", routes)
sheet("Запуски", runs)
sheet("Повторный запуск", verification)
wb.save(EDA / "проверка_источников.xlsx")

md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb = nbf.v4.new_notebook()
nb.metadata["kernelspec"] = {"display_name": "Python (APL news)", "language": "python", "name": "apl-news"}
nb.cells = [
 md("# Описание источников\n\nПроверка способов получения и качества сохранения новостей. Окно публикаций: **26 октября — 8 ноября 2026**, Москва; конец 9 ноября исключён. Это обзор сбора, основной EDA новостей — следующий этап.\n\nКаждая из 34 строк исходного Excel имеет результат. Сайты клубов и источники только структурированных данных исключены по решению пользователя. Наличие RSS не доказывает полноту архива; HTTP 200 не доказывает наличие текста.\n\nДаты публикации заявлены источниками. Текущая редакция статьи могла измениться позже; `first_seen_at` относится к нашему сбору сейчас, не к моменту первого появления в интернете."),
 code("from pathlib import Path\nimport json, html\nimport pandas as pd\nfrom IPython.display import display, HTML\nROOT = Path.cwd()\nif ROOT.name == 'EDA':\n    ROOT = ROOT.parent\nassert (ROOT / 'data').is_dir(), 'Запустите ноутбук из корня проекта или EDA/'\nSHOW_FULL_TEXT = False  # True: локально показать полные тексты из news.csv\naudit = pd.read_csv(ROOT / 'EDA' / 'source_audit.csv').fillna('')\ndisplay(audit[['source','status','news','partial','review','posts','rerun_passed']])"),
 md("## Что и как проверено\n\nRSS/Atom — быстрый поиск новых URL; публичный API — даты, пагинация и текст, если доступен; HTML — извлечение статьи; карты сайта и архивы — поиск истории. У каждого способа есть отдельные результаты в Excel. Пустое окно, запрет доступа, ошибка парсера и отсутствие английских статей — разные причины. Платные API не подключались."),
 code("display(audit[['source','rss','api','html','archive_limits','reason']])\nroutes = pd.read_json(ROOT / 'EDA' / 'source_routes.json')\ndisplay(routes.groupby(['kind','status'], dropna=False).size().rename('проверок').reset_index())"),
 md("## Сохранённые наборы\n\n`news.csv` — статьи, прошедшие текущий фильтр АПЛ; `posts.csv` — отдельный формат коротких постов; `partial.csv` — неполные тексты; `review.csv` — неопределённые случаи; `excluded.csv` — исключённые форматы/языки. Одна строка — публикация. CSV хранит полные тексты локально, вложенные списки представлены JSON-строками. Повторный запуск добавляет новые URL; уже сохранённые строки не теряются. Для обновления текста существующей статьи используется `--refresh-existing`; прежняя редакция сохраняется в revisions.jsonl."),
 code("frames = {}\nfor folder in sorted((ROOT / 'data').iterdir()):\n    if not folder.is_dir():\n        continue\n    for name in ['news','posts','partial','review','excluded']:\n        path = folder / f'{name}.csv'\n        if path.exists():\n            frames[(folder.name,name)] = pd.read_csv(path).fillna('')\nsummary = pd.DataFrame([{'source_id':s, 'dataset':k, 'rows':len(df)} for (s,k),df in frames.items()])\ndisplay(summary.pivot(index='source_id',columns='dataset',values='rows').fillna(0).astype(int))"),
 md("## Примеры каждого источника\n\nПо два примера, где они есть. Сначала берутся статьи из основного набора, затем случаи на проверку/неполные публикации. Это проверка качества извлечения, не случайная выборка для оценки долей тем. По умолчанию показывается начало текста; для полного текста переключите SHOW_FULL_TEXT. Разметка АПЛ автоматическая и предварительная; наличие клуба в начале текста ещё не доказывает полезность для конкретного матча."),
 code("for _, source in audit.iterrows():\n    display(HTML('<h3>' + html.escape(str(source['source'])) + '</h3>'))\n    samples = []\n    for kind in ['news','review','partial','posts','excluded']:\n        df = frames.get((source['source_id'],kind))\n        if df is not None and len(df):\n            for _, row in df.head(2-len(samples)).iterrows():\n                samples.append((kind,row))\n        if len(samples) == 2:\n            break\n    if not samples:\n        display(HTML('<p>' + html.escape(str(source['status']) + ': ' + str(source['reason'])) + '</p>'))\n    for kind,row in samples:\n        text = str(row['text'])\n        preview = text if SHOW_FULL_TEXT else text[:450] + ('…' if len(text)>450 else '')\n        details = {key:row.get(key,'') for key in ['published_at','date_provenance','source_tags','selection_reason','text_status','extraction_method']}\n        display(HTML('<p><b>' + html.escape(str(row['title'])) + '</b> [' + kind + ']</p>' + '<p><a href=\"' + html.escape(str(row['url']),quote=True) + '\">Оригинал</a></p>' + '<pre style=\"white-space:pre-wrap\">' + html.escape(preview) + '</pre>'))\n        display(pd.Series(details).to_frame('значение'))"),
 md("## Проверки повторного запуска и целостности\n\nВажны отсутствие дублей по каноническому URL, сохранение старых записей и корректный перенос полного текста через CSV. Проверка не доказывает полноту сайта или постоянную доступность источника. Временные ошибки повторяются, а исходы и ограничения сохраняются в журналах."),
 code("checks_path = ROOT / 'research' / 'rerun_checks.json'\nchecks = pd.read_json(checks_path) if checks_path.exists() else pd.DataFrame()\ndisplay(checks)\nfor (source,kind), df in frames.items():\n    assert not df['url'].duplicated().any(), (source,kind,'duplicate URL')\n    if kind == 'news' and len(df):\n        dates = pd.to_datetime(df['published_at'], utc=True, errors='raise')\n        assert dates.notna().all()\n        assert (df['text_status']=='full_text').all()\n        assert (df['content_type']=='article').all()\nif len(checks):\n    assert checks['passed'].all(), 'Есть неуспешные проверки повторного запуска'\nprint('Проверки структуры сохранённых CSV пройдены.')"),
 md("## Ограничения и дальнейшая работа\n\nПолнота охвата сайтов не утверждается. `archive_limits` показывает исчерпание бюджета страниц; RSS часто содержит только недавние материалы. Отсутствие меток игрока нельзя компенсировать выдуманной принадлежностью к клубу — такие статьи остаются на проверку. Текущий справочник клубов взят из сохранённого аудита проекта от 26 октября 2026. Короткие посты журналистов сохраняются отдельно, если есть рабочий разрешённый доступ; закрытый доступ не заменяется пересказами.\n\nСледующий этап: проверить 100–150 предварительно размеченных публикаций отдельным агентом-судьёй, затем человеком; после этого исследовать типы новостей, пересечения и покрытие клубов. Агент-судья запускается пользователем. Числовой рейтинг авторитетности и скорость относительно реального события сейчас не установлены.")
]
nb.cells[8:8] = [
 md('## Примеры проблем и точных повторов\n\nНеполные тексты, неоднозначный отбор и исключённые страницы смотрим отдельно. Совпадения URL или хеша текста — техническая проверка дубликатов, а не группировка новостей об одном событии.'),
 code("for kind in ['partial','review','excluded']:\n    available = [df for (source,name),df in frames.items() if name==kind and len(df)]\n    display(HTML('<h4>' + kind + '</h4>'))\n    if available:\n        examples = pd.concat(available,ignore_index=True).groupby('source_id',sort=True).head(1).head(5)\n        display(examples[['source','title','published_at','url','text_status','selection_reason','exclusion_reason']])\n    else:\n        print('Нет сохранённых примеров')\nnews_frames = [df for (source,name),df in frames.items() if name=='news' and len(df)]\nif news_frames:\n    merged = pd.concat(news_frames,ignore_index=True)\n    duplicates = merged[merged.duplicated('url',keep=False) | merged.duplicated('content_hash',keep=False)]\n    display(duplicates[['source','title','url','content_hash']].head(20))\n    print('Строк с точными совпадениями URL/текста:',len(duplicates))")
]
# ISO timestamps may legitimately differ in fractional-second precision.
for cell in nb.cells:
    if cell.cell_type == 'code':
        cell.source = cell.source.replace("utc=True, errors='raise'", "utc=True, format='ISO8601', errors='raise'")
        cell.source = cell.source.replace("df.head(2-len(samples)).iterrows()", "pd.concat([df.head(1),df.tail(1)]).drop_duplicates('url').head(2-len(samples)).iterrows()")
        cell.source = cell.source.replace("'published_at','date_provenance'", "'published_at','published_date_display','date_precision','date_provenance'")
nbf.write(nb, EDA / "описание источников.ipynb")
print(json.dumps({"sources": len(summary), "scripts": sum(bool(r['script']) for r in summary),
                  "news": sum(r['news'] for r in summary), "notebook": str(EDA / 'описание источников.ipynb')}, ensure_ascii=False))
