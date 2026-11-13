"""Shared output options and phrase input normalization for publisher searches."""
import html
import re
import unicodedata

FILTER_MODES = {"官网语法": "native", "任一词组": "any", "全部词组": "all"}
OUTPUT_MODES = {"仅 XML": "xml", "XML + PDF": "xml_pdf", "仅 PDF": "pdf"}


def normalize(value):
    value = re.sub(r"<[^>]+>", " ", html.unescape(str(value)))
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    return " ".join(re.findall(r"[^\W_]+", value))


def phrases(value):
    return list(dict.fromkeys(normalize(term) for term in re.split(r"[,，;；\n]+", value) if normalize(term)))
