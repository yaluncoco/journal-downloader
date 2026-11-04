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
    return " ".join(re.findall(r"[a-z0-9]+", text))


@lru_cache(maxsize=1)
def catalog():
    path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "journals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("count") != len(data.get("journals", [])):
        raise ValueError("期刊目录文件不完整，请重新安装软件。")
    return data


@lru_cache(maxsize=1)
def search_index():
    return tuple((item, normalize(" ".join([item["name"], *item.get("aliases", [])])), item["issn"].replace("-", "").casefold())
                 for item in catalog()["journals"])


def search(query="", scope="全部"):
    if query.strip().casefold() == "rse":
        query = "0034-4257"
    words = normalize(query).split()
    compact = "".join(words)
    result = []
    for item, title, issn in search_index():
        if scope == "在刊" and item["status"] != "Active":
            continue
        if scope == "历史刊名" and item["status"] in ("Active", "Forthcoming"):
            continue
        if not words or all(word in title for word in words) or compact in issn:
            rank = (0 if title == " ".join(words) or issn == compact else
                    1 if title.startswith(" ".join(words)) else 2)
            result.append((rank, item["status"] != "Active", title, item))
    return [item[-1] for item in sorted(result, key=lambda entry: entry[:3])]


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
