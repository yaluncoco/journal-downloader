"""Sanitized Elsevier XML to print HTML, including CALS tables and MathML."""
from __future__ import annotations

import html
import base64
import hashlib
import re
import xml.etree.ElementTree as ET

from xml_utils import inspect_xml, local, normalized_text

MATH_NS = "http://www.w3.org/1998/Math/MathML"
MATH_TAGS = set("math mrow mi mo mn mtext mspace ms mfrac msqrt mroot mstyle merror mpadded mphantom mfenced menclose msub msup msubsup munder mover munderover mmultiscripts mprescripts none mtable mtr mtd mlabeledtr semantics".split())
MATH_ATTRS = set("display mathvariant mathsize mathcolor stretchy symmetric fence separator largeop movablelimits accent accentunder lspace rspace form width height depth linethickness bevelled numalign denomalign columnalign rowalign columnspacing rowspacing columnspan rowspan notation scriptlevel displaystyle subscriptshift superscriptshift".split())


def first(node, name):
    return next((n for n in node.iter() if local(n) == name), None) if node is not None else None


def text(node, name=None):
    if name:
        node = first(node, name)
    return normalized_text(node) if node is not None else ""


def esc(value):
    return html.escape(value or "", quote=True)


def ident(node):
    return ' id="' + esc(node.get("id")) + '"' if node.get("id") else ""


def math_html(node):
    tag = local(node)
    if tag in ("annotation", "annotation-xml"):
        return ""
    if tag not in MATH_TAGS:
        raise ValueError(f"尚不支持公式节点 {tag}，为避免公式失真，本篇保持待处理。")
    # Chromium implements MathML Core; legacy mfenced must be expanded explicitly.
    if tag == "mfenced":
        separators = node.get("separators", ",").replace(" ", "")
        pieces = []
        for i, child in enumerate(node):
            if i and separators:
                pieces.append("<mo>" + esc(separators[min(i - 1, len(separators) - 1)]) + "</mo>")
            pieces.append(math_html(child))
        return "<mrow><mo>" + esc(node.get("open", "(")) + "</mo>" + "".join(pieces) + "<mo>" + esc(node.get("close", ")")) + "</mo></mrow>"
    attrs = "".join(f' {k}="{esc(v)}"' for k, v in node.attrib.items() if k in MATH_ATTRS)
    if tag == "math":
        attrs += f' xmlns="{MATH_NS}"'
    content = esc(node.text) + "".join(math_html(c) + esc(c.tail) for c in node)
    return f"<{tag}{attrs}>{content}</{tag}>"


class Article:
    def __init__(self, data):
        self.root = ET.fromstring(data)
        core = first(self.root, "coredata")
        self.doi = text(core, "doi").lower()
        info = inspect_xml(data, self.doi)
        if info["status"] != "fulltext_structured":
            raise ValueError("需要含结构化正文的 XML。rawtext/仅摘要文件无法可靠还原图表，已留待处理。")
        original = first(self.root, "originalText")
        self.article = next((node for node in original.iter()
                             if local(node) in ("article", "simple-article")), None) if original is not None else None
        if self.article is None:
            raise ValueError("XML 缺少受支持的 article/simple-article 结构，已保留原文件。")
        self.head = next((node for node in self.article if local(node) in ("head", "simple-head")), None)
        self.body = first(self.article, "body")
        self.tail = first(self.article, "tail")
        self.title = text(self.head, "title") or text(core, "title")
        self.journal = text(core, "publicationName")
        self.year = text(core, "coverDate")[:4]
        self.volume = text(core, "volume")
        self.number = text(core, "article-number") or text(core, "startingPage")
        floats = first(self.article, "floats")
        self.floats = {n.get("id"): n for n in floats} if floats is not None else {}
        self.placed = set()
        self.images = {}
        self.inline_heights = {}
        for node in self.root.iter():
            if local(node) == "object" and node.get("type") == "IMAGE-DOWNSAMPLED":
                height = node.get("height", "")
                if height.isdecimal() and 1 <= int(height) <= 10000:
                    # The standard image is a text-scale rendition. High-resolution
                    # bytes improve sharpness but must never determine printed size.
                    self.inline_heights[node.get("ref", "")] = min(6, max(.5, int(height) / 12))

    def required_images(self):
        refs = set()
        for node in self.article.iter():
            if local(node) in ("figure", "inline-figure", "inline-graphic", "graphic"):
                links = [n.get("locator") for n in node.iter() if local(n) == "link" and n.get("locator")]
                if not links:
                    raise ValueError("图像缺少 locator，无法确保图像完整性。")
                refs.update(links)
        return sorted(refs, key=lambda ref: (re.sub(r"\d+", "", ref), int(re.search(r"\d+", ref).group()) if re.search(r"\d+", ref) else 0))

    def inner(self, node):
        return esc(node.text) + "".join(self.inline(c) + esc(c.tail) for c in node)

    def inline(self, node):
        tag = local(node)
        if tag == "math":
            return math_html(node)
        if tag in ("float-anchor", "alt-text", "contributor-role"):
            return ""
        if tag in ("inline-figure", "inline-graphic", "graphic"):
            return self.pictures(node, inline=True)
        if tag in ("cross-ref", "cross-refs"):
            ref = node.get("refid", "").split(" ")[0]
            return f'<a href="#{esc(ref)}">{self.inner(node)}</a>'
        if tag == "br":
            return "<br>"
        mapped = {"italic": "i", "bold": "b", "sup": "sup", "inf": "sub", "sub": "sub", "monospace": "code", "underline": "u", "small-caps": "span"}.get(tag)
        if mapped:
            return f"<{mapped}>{self.inner(node)}</{mapped}>"
        if tag in ("display", "formula", "table", "figure", "list"):
            return self.block(node)
        return self.inner(node)

    def pictures(self, node, inline=False):
        result = []
        for link in node.iter():
            if local(link) != "link" or not link.get("locator"):
                continue
            ref = link.get("locator")
            path = self.images.get(ref)
            if not path:
                raise ValueError(f"图片 {ref} 缺失，无法生成完整 PDF。")
            size = f' style="height:{self.inline_heights.get(ref, .95):.4f}em"' if inline else ""
            result.append(f'<img class="{"inline-image" if inline else "figure-image"}"{size} src="{esc(path.resolve().as_uri())}" alt="{esc(ref)}">')
        return "".join(result)

    def table(self, node):
        caption = f'<b>{esc(text(node, "label"))}</b> {self.inner(first(node, "caption")) if first(node, "caption") is not None else ""}'
        output = [f'<div class="wide table-block"{ident(node)}>']
        caption_written = False
        for group in (n for n in node if local(n) == "tgroup"):
            cols = max(1, min(100, int(group.get("cols", "1"))))
            specs = [n for n in group if local(n) == "colspec"]
            mapping = {n.get("colname", f"col{i + 1}"): int(n.get("colnum", str(i + 1))) - 1 for i, n in enumerate(specs)}
            spans = {n.get("spanname"): (n.get("namest"), n.get("nameend")) for n in group if local(n) == "spanspec"}
            occupied = [0] * cols
            output.append('<table>')
            if not caption_written:
                output.append('<caption class="caption">' + caption + '</caption>')
                caption_written = True
            for part in group:
                part_tag = local(part)
                if part_tag not in ("thead", "tbody", "tfoot"):
                    continue
                output.append(f"<{part_tag}>")
                for row in part:
                    if local(row) != "row":
                        continue
                    output.append("<tr>")
                    cursor = 0
                    for entry in row:
                        if local(entry) != "entry":
                            continue
                        while cursor < cols and occupied[cursor]:
                            cursor += 1
                        span_start, span_end = spans.get(entry.get("spanname"), (None, None))
                        start = mapping.get(entry.get("namest", span_start or entry.get("colname")), cursor)
                        end = mapping.get(entry.get("nameend", span_end), start)
                        if start >= cols or end < start or end >= cols:
                            raise ValueError("表格列结构无效，需单独检查。")
                        # Explicit column positions can leave intentional blank cells.
                        while cursor < start:
                            if not occupied[cursor]:
                                output.append("<td></td>")
                            cursor += 1
                        rowspan = max(1, int(entry.get("morerows", "0")) + 1)
                        for index in range(start, end + 1):
                            occupied[index] = rowspan
                        cell = "th" if part_tag == "thead" else "td"
                        output.append(f'<{cell} colspan="{end-start+1}" rowspan="{rowspan}">{self.inner(entry)}</{cell}>')
                        cursor = end + 1
                    output.append("</tr>")
                    occupied = [max(0, n - 1) for n in occupied]
                output.append(f"</{part_tag}>")
            output.append("</table>")
        for note in node:
            if local(note) in ("table-footnote", "legend"):
                output.append('<div class="table-note">' + self.inner(note) + "</div>")
        return "".join(output) + "</div>"

    def float(self, ref):
        if ref in self.placed:
            return ""
        node = self.floats.get(ref)
        if node is None:
            raise ValueError(f"图表锚点 {ref} 未找到对应内容。")
        self.placed.add(ref)
        return self.block(node)

    def paragraph(self, node):
        # Keep block formulas out of p, and place floats immediately after their anchor paragraph.
        chunks, current = [], esc(node.text)
        for child in node:
            if local(child) in ("display", "formula", "table", "figure", "list"):
                if current.strip():
                    chunks.append("<p>" + current + "</p>")
                chunks.append(self.block(child))
                current = esc(child.tail)
            else:
                current += self.inline(child) + esc(child.tail)
        if current.strip():
            chunks.append("<p>" + current + "</p>")
        for anchor in node.iter():
            if local(anchor) == "float-anchor":
                chunks.append(self.float(anchor.get("refid")))
        return "".join(chunks)

    def block(self, node, level=2):
        tag = local(node)
        if tag in ("para", "simple-para", "note-para"):
            return self.paragraph(node)
        if tag == "table":
            return self.table(node)
        if tag == "figure":
            caption = first(node, "caption")
            return f'<figure class="wide"{ident(node)}>{self.pictures(node)}<figcaption><b>{esc(text(node, "label"))}</b> {self.inner(caption) if caption is not None else ""}</figcaption></figure>'
        if tag == "float-anchor":
            return self.float(node.get("refid"))
        if tag == "formula":
            content = "".join(self.inline(c) + esc(c.tail) for c in node if local(c) != "label")
            return f'<div class="equation"{ident(node)}><div class="equation-body">{content}</div><span>{esc(text(node, "label"))}</span></div>'
        if tag == "display":
            return "".join(self.block(c, level) + esc(c.tail) for c in node)
        if tag == "math":
            return math_html(node)
        if tag == "bib-reference":
            source = first(node, "source-text")
            if source is not None:
                value = self.inner(source)
            else:
                reference = first(node, "reference")
                value = esc(" ".join(t.strip() for t in (reference if reference is not None else node).itertext() if t.strip()))
            return f'<p class="reference"{ident(node)}>{value}</p>'
        if tag == "list":
            return '<ul>' + "".join('<li>' + self.inner(c) + '</li>' for c in node if local(c) == "list-item") + '</ul>'
        if tag == "section-title":
            return f"<h{min(level, 4)}{ident(node)}>{self.inner(node)}</h{min(level, 4)}>"
        if tag in ("alt-text", "label"):
            return ""
        if tag == "e-component":
            return '<p class="supplement">Supplementary material: ' + esc(text(node, "caption")) + ' (separate attachment; not embedded in this PDF).</p>'
        titles = [n for n in node if local(n) == "section-title"]
        output = []
        if titles:
            label = next((text(n) for n in node if local(n) == "label"), "")
            output.append(f'<h{min(level, 4)}{ident(node)}>{esc(label)} {self.inner(titles[0])}</h{min(level, 4)}>')
        for child in node:
            if local(child) in ("section-title", "label") and titles:
                continue
            output.append(self.block(child, level + 1 if tag == "section" else level))
        if not len(node):
            return esc(node.text)
        return "".join(output)

    def render(self, images, layout="double"):
        self.images, self.placed = images, set()
        author_groups = [n for n in self.head if local(n) == "author-group"] if self.head is not None else []
        authors = []
        affiliations = []
        for group in author_groups:
            for author in (n for n in group.iter() if local(n) == "author"):
                name = " ".join(text(author, t) for t in ("given-name", "surname")).strip()
                refs = "".join(self.inline(n) for n in author if local(n) == "cross-ref")
                authors.append(esc(name) + refs)
            for affiliation in (n for n in group.iter() if local(n) == "affiliation"):
                value = next((n for n in affiliation if local(n) in ("textfn", "source-text")), None)
                if value is not None:
                    label = next((text(n) for n in affiliation if local(n) == "label"), "")
                    affiliations.append(f'<div{ident(affiliation)}><sup>{esc(label)}</sup> {self.inner(value)}</div>')
        abstract = "".join(self.block(n) for n in self.head if local(n) == "abstract" and n.get("class", "author") == "author") if self.head is not None else ""
        keywords = [text(n) for n in self.head.iter() if local(n) == "keyword"] if self.head is not None else []
        body = self.block(self.body) if self.body is not None else ""
        graphical = "".join(self.block(n) for n in self.head if local(n) == "abstract" and n.get("class", "author") != "author") if self.head is not None else ""
        # Some XML versions put data availability and footnotes in the article head.
        extra = "".join(self.block(n) for n in self.head if local(n) in ("data-availability", "footnote")) if self.head is not None else ""
        for ref in self.floats:
            body += self.float(ref)
        references = self.block(self.tail) if self.tail is not None else ""
        copyright_text = text(first(self.article, "item-info"), "copyright")
        citation = " ".join(v for v in (self.journal, self.volume, f"({self.year})" if self.year else "", self.number) if v)
        css = STYLE.replace("__COLUMNS__", "2" if layout == "double" else "1")
        # CSS strings do not inherit HTML escaping, so use only a strict DOI subset in page margins.
        safe_doi = re.sub(r"[^a-zA-Z0-9./()_-]", "", self.doi)
        css = css.replace("__DOI__", safe_doi)
        script_hash = base64.b64encode(hashlib.sha256(FIT_EQUATIONS.encode()).digest()).decode()
        return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src file: data:; style-src 'unsafe-inline'; font-src file: data:; script-src 'sha256-{script_hash}'; base-uri 'none'">
<title>{esc(self.title)}</title><style>{css}</style></head><body>
<header><div class="citation">{esc(citation)}</div><div class="journal">{esc(self.journal)}</div><div class="edition">Research article · XML reading edition</div></header>
<h1>{esc(self.title)}</h1><div class="authors">{', '.join(authors)}</div><div class="affiliations">{''.join(affiliations)}</div>
<div class="front"><aside><h3>ARTICLE INFORMATION</h3><div class="doi">DOI: {esc(self.doi)}</div><h3>Keywords</h3>{'<br>'.join(esc(k) for k in keywords)}</aside><section class="abstract"><h3>ABSTRACT</h3>{abstract}</section></div>
<main>{graphical}{body}{extra}{references}</main><div class="copyright">{esc(copyright_text)}<br>Reconstructed from the authorized XML and figure objects. This is not the publisher PDF. Supplementary files are separate.</div>
<script>{FIT_EQUATIONS}</script></body></html>'''


FIT_EQUATIONS = '''
function fitEquations() {
  for (const row of document.querySelectorAll('.equation')) {
    const body = row.querySelector('.equation-body');
    const label = row.lastElementChild;
    const needed = () => {
      const range = document.createRange(); range.selectNodeContents(body);
      return Math.max(body.scrollWidth, range.getBoundingClientRect().width);
    };
    const available = () => row.getBoundingClientRect().width - label.getBoundingClientRect().width - 12;
    row.style.fontSize = '9pt';
    if (needed() > available()) {
      const size = Math.max(7, 9 * available() / needed() * .97);
      row.style.fontSize = size + 'pt';
      if (needed() > available() + 1) {
        row.classList.add('wide'); row.style.fontSize = '9pt';
        if (needed() > available()) row.style.fontSize = Math.max(6, 9 * available() / needed() * .97) + 'pt';
      }
    }
  }
}
window.addEventListener('load', fitEquations);
window.addEventListener('beforeprint', fitEquations);
'''


STYLE = r'''
@page { size: A4; margin: 15mm 16mm 17mm;
  @top-right { content: "XML reading edition"; font: italic 7pt Arial; color: #777; }
  @bottom-left { content: "__DOI__  |  Reconstructed from XML"; font: 7pt Arial; color: #666; }
  @bottom-right { content: counter(page); font: 8pt Arial; color: #444; }
}
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: #141414; font: 10pt/1.25 "Times New Roman", "Cambria", serif; }
@media screen { body { width: 178mm; margin: 15mm auto; } }
header { border-top: 1.4pt solid #253c48; border-bottom: .6pt solid #a5b2b8; padding: 0 0 9pt; margin-bottom: 17pt; }
.citation { text-align: right; color: #575d60; font: 8pt Arial; padding: 5pt 0 10pt; }
.journal { font: 19pt/1.1 "Times New Roman"; color: #183c4e; }
.edition { font: 8pt Arial; color: #777; margin-top: 5pt; }
h1 { font: bold 18pt/1.13 "Times New Roman"; margin: 0 0 12pt; }
.authors { font-size: 11pt; margin-bottom: 7pt; }
.affiliations { font: italic 8.5pt/1.25 "Times New Roman"; margin-bottom: 16pt; }
.front { display: grid; grid-template-columns: 40mm 1fr; column-gap: 8mm; border-top: .5pt solid #999; border-bottom: .5pt solid #999; padding: 11pt 0; margin-bottom: 14pt; }
.front aside { font-size: 8pt; line-height: 1.35; }
.front h3 { font: 8pt Arial; letter-spacing: 1pt; margin: 0 0 8pt; }
.front aside h3:not(:first-child) { margin-top: 13pt; letter-spacing: 0; font-style: italic; }
.doi { overflow-wrap: anywhere; }
.abstract { font-size: 9pt; }
.abstract p { margin: 0 0 6pt; text-indent: 0; }
main { columns: __COLUMNS__; column-gap: 7mm; column-fill: balance; }
p { margin: 0 0 5pt; text-align: justify; text-indent: 9pt; orphans: 3; widows: 3; overflow-wrap: break-word; }
h2, h3, h4 { break-after: avoid; margin: 10pt 0 6pt; font-size: 10pt; line-height: 1.2; }
h3, h4 { font-style: italic; font-weight: normal; }
a { color: #154a66; text-decoration: none; }
sup, sub { font-size: 72%; line-height: 0; }
math { font-family: "Cambria Math", math; font-size: 1em; }
.equation { display: flex; align-items: center; justify-content: space-between; gap: 5pt; margin: 9pt 0; break-inside: avoid; font-size: 9pt; }
.equation-body { min-width: 0; }
.equation > span { flex: none; white-space: nowrap; }
.equation-body math { max-width: 100%; }
.wide { column-span: all; }
figure { margin: 11pt 0 12pt; break-inside: avoid; text-align: center; }
.figure-image { display: block; max-width: 100%; max-height: 162mm; width: auto; height: auto; margin: 0 auto 5pt; }
figcaption, .caption { text-align: justify; font: 8.5pt/1.2 "Times New Roman"; }
figcaption p, .caption p { text-indent: 0; display: inline; }
.inline-image { display: inline-block; width: auto; height: .95em; max-width: 100%; object-fit: contain; object-position: left bottom; vertical-align: baseline; }
.table-block { margin: 11pt 0 12pt; }
.caption { margin-bottom: 5pt; break-after: avoid; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; font: 8pt/1.18 "Times New Roman"; border-top: .7pt solid #333; border-bottom: .7pt solid #333; }
thead { display: table-header-group; border-bottom: .5pt solid #555; break-inside: avoid; break-after: avoid; }
thead tr { break-after: avoid; }
tbody tr:first-child { break-before: avoid; }
tbody tr:nth-last-child(2) { break-after: avoid; }
tbody tr:last-child { break-before: avoid; }
tr { break-inside: avoid; }
th, td { text-align: left; vertical-align: top; padding: 4pt 3pt; overflow-wrap: anywhere; }
th { font-weight: normal; }
tbody tr:nth-child(even) { background: #f4f5f5; }
td p { margin: 0; text-indent: 0; text-align: left; }
.table-note { font-size: 8pt; margin-top: 4pt; }
.reference { font-size: 8pt; line-height: 1.22; padding-left: 9pt; text-indent: -9pt; margin-bottom: 4pt; text-align: left; overflow-wrap: anywhere; }
ul { padding-left: 14pt; }
.supplement { font-size: 8.5pt; color: #444; text-indent: 0; }
.copyright { border-top: .5pt solid #aaa; margin-top: 15pt; padding-top: 7pt; font: 7pt/1.3 Arial; color: #666; }
'''
