"""Predownload title phrases. Full-text/author keywords are not presumed available."""
import html
import re
import unicodedata

FILTER_MODES = {"任一词组": "any", "全部词组": "all"}
OUTPUT_MODES = {"仅 XML": "xml", "XML + PDF": "xml_pdf", "仅 PDF": "pdf"}


def normalize(value):
    value = re.sub(r"<[^>]+>", " ", html.unescape(str(value)))
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    return " ".join(re.findall(r"[^\W_]+", value))


def phrases(value):
    return list(dict.fromkeys(normalize(term) for term in re.split(r"[,，;；\n]+", value) if normalize(term)))


def filter_records(records, query="", mode="any"):
    if mode not in ("any", "all"):
        raise ValueError("筛选方式无效")
    terms = phrases(query)
    if query.strip() and not terms:
        raise ValueError("请填写有效的题名关键词，多个词组用逗号分隔。")
    if not terms:
        return list(records)
    patterns = [re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)") if term.isascii() else re.compile(re.escape(term)) for term in terms]
    combine = any if mode == "any" else all
    return [row for row in records if combine(pattern.search(normalize(row.get("title", ""))) for pattern in patterns)]
