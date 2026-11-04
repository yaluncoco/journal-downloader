"""Build the bundled catalogue from Elsevier's public ScienceDirect XLSX lists.

Run: python build_catalog.py --source-dir path/to/downloaded/lists
Add --download to refresh those official source files (no API Key required).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

SOURCE_PAGE = "https://www.elsevier.com/products/sciencedirect/journals"
SOURCE_ROOT = "https://legacyfileshare.elsevier.com/promis_misc/sd-content/journals/"
LISTS = (("jnlactive", "Active"), ("jnlforthcoming", "Forthcoming"), ("jnlpubchanges", "Historical"))
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def rows(path):
    with zipfile.ZipFile(path) as archive:
        strings = (["".join(n.itertext()) for n in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
                   if "xl/sharedStrings.xml" in archive.namelist() else [])
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        headers = None
        for row in root.findall("x:sheetData/x:row", NS):
            cells = {}
            for cell in row:
                column = re.sub(r"\d", "", cell.get("r", ""))
                if cell.get("t") == "inlineStr":
                    value = "".join(cell.find("x:is", NS).itertext())
                else:
                    value = cell.findtext("x:v", "", NS)
                    if cell.get("t") == "s" and value:
                        value = strings[int(value)]
                cells[column] = value.strip()
            if headers is None:
                headers = cells
            else:
                yield {title: cells.get(column, "") for column, title in headers.items()}


def build(source_dir, output):
    journals, sources = {}, []
    for name, status in LISTS:
        path = source_dir / (name + ".xlsx")
        data = path.read_bytes()
        count, excluded = 0, 0
        for row in rows(path):
            title = row.get("Full Title") or row.get("Journal Title")
            if not title:
                continue
            count += 1
            issn = row.get("ISSN", "").upper()
            if not re.fullmatch(r"\d{4}-\d{3}[\dX]", issn):
                issn = ""
            key = issn or title.casefold()
            url = row.get("Shortcut URL") or ("https://www.sciencedirect.com/science/journal/" + issn.replace("-", "")
                if issn else "https://www.sciencedirect.com/search?" + urllib.parse.urlencode({"pub": title}))
            if "/science/handbooks/" in url or "/bookseries/" in url:
                excluded += 1
                continue
            item = {"name": title, "issn": issn, "url": url,
                    "status": status if status != "Historical" else row.get("Status") or status,
                    "history": row.get("Change History", ""), "change_year": row.get("Date of change", ""), "aliases": []}
            if key not in journals:
                journals[key] = item
            else:
                if item["history"] and not journals[key]["history"]:
                    journals[key]["history"] = item["history"]
                if title != journals[key]["name"] and title not in journals[key]["aliases"]:
                    journals[key]["aliases"].append(title)
        sources.append({"url": SOURCE_ROOT + path.name, "sha256": hashlib.sha256(data).hexdigest(),
                        "rows": count, "excluded_non_journals": excluded})
    records = sorted(journals.values(), key=lambda item: item["name"].casefold())
    result = {"schema": 1, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "source_page": SOURCE_PAGE, "sources": sources, "count": len(records), "journals": records}
    assert len(records) > 4000, "Official lists appear incomplete"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(records), "source_rows": [s["rows"] for s in sources]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "journals.json")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        args.source_dir.mkdir(parents=True, exist_ok=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for name, _ in LISTS:
            request = urllib.request.Request(SOURCE_ROOT + name + ".xlsx",
                headers={"User-Agent": "Mozilla/5.0", "Referer": SOURCE_PAGE})
            with opener.open(request, timeout=45) as response:
                payload = response.read(20 * 1024 * 1024 + 1)
                if len(payload) > 20 * 1024 * 1024:
                    raise ValueError("Source list exceeds size limit")
            (args.source_dir / (name + ".xlsx")).write_bytes(payload)
    build(args.source_dir, args.output)
