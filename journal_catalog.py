"""Offline, searchable snapshot of the official ScienceDirect journal lists."""
from functools import lru_cache
import html
import json
from pathlib import Path
import re
import sys
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFKD", html.unescape(text).casefold().replace("&", " and "))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text))


def tokens(text):
    # Conservative equivalences for common journal-title plurals. Do not stem
    # arbitrary words (e.g. analysis, physics) or conflate all substring matches.
    plurals = {"computers": "computer", "systems": "system", "applications": "application",
               "sciences": "science", "technologies": "technology", "forests": "forest"}
    return tuple(plurals.get(word, word) for word in normalize(text).split())


def title_rank(query, title):
    if query == title:
        return (0, 0)
    if len(query) <= len(title) and title[:len(query)-1] == query[:-1] and title[len(query)-1].startswith(query[-1]):
        return (1, len(title) - len(query))
    for start in range(len(title) - len(query) + 1):
        if title[start:start+len(query)] == query:
            return (2, start)
    if all(any(word.startswith(q) for word in title) for q in query):
        return (3, len(title))
    return None


@lru_cache(maxsize=1)
def catalog():
    path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "journals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("count") != len(data.get("journals", [])):
        raise ValueError("期刊目录文件不完整，请重新安装软件。")
    return data


@lru_cache(maxsize=1)
def search_index():
    return tuple((item, tokens(item["name"]), tuple(tokens(a) for a in item.get("aliases", [])), item["issn"].replace("-", "").casefold())
                 for item in catalog()["journals"])


def search(query="", scope="全部", category="全部学科", zone="全部分区", top=False):
    from journal_metadata import category_matches, reference_matches
    if query.strip().casefold() == "rse":
        query = "0034-4257"
    words = tokens(query)
    compact = "".join(words)
    if query.strip() and not words:
        return []
    result = []
    for item, title, aliases, issn in search_index():
        if scope == "在刊" and item["status"] != "Active":
            continue
        if scope == "历史刊名" and item["status"] in ("Active", "Forthcoming"):
            continue
        if not category_matches(item, category) or not reference_matches(item, zone, top):
            continue
        if not words:
            rank = (0, 0, 0)
        elif issn == compact:
            rank = (-1, 0, 0)
        else:
            main = title_rank(words, title)
            ranks = [(main[0], 0, main[1])] if main is not None else []
            for alias in aliases:
                match = title_rank(words, alias)
                if match is not None:
                    ranks.append((match[0], 1, match[1]))
            if compact and compact[0].isdigit() and compact in issn:
                ranks.append((2, 0, 0))
            if not ranks:
                continue
            rank = min(ranks)
        result.append((rank, item["status"] != "Active", normalize(item["name"]), item))
    return [entry[-1] for entry in sorted(result, key=lambda entry: entry[:3])]


@lru_cache(maxsize=1)
def lookup_index():
    by_issn, by_name = {}, {}
    for item in catalog()["journals"]:
        if item["issn"]:
            by_issn[item["issn"].replace("-", "")] = item
        for name in (item["name"], *item.get("aliases", [])):
            matches = by_name.setdefault(normalize(name).replace(" ", ""), [])
            if item not in matches:
                matches.append(item)
    return by_issn, by_name


def lookup(slug, title):
    by_issn, by_name = lookup_index()
    if slug in by_issn:
        return by_issn[slug]
    matches = by_name.get(normalize(title).replace(" ", ""), [])
    return matches[0] if len(matches) == 1 else None


def status_label(status):
    return {"Active": "在刊", "Forthcoming": "即将出版", "Changed Name": "已更名",
            "Transferred": "已转出", "Discontinued": "已停刊", "Historical": "历史刊名",
            "Ceased": "已停刊", "Incorporated": "已合并"}.get(status, status)
