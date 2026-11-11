"""Self-contained single-column reading PDFs; no browser or network access.

Consumes only Article.render's sanitized HTML. MathML is typeset with the bundled
STIX math font; figures remain local verified image files. Unsupported content
raises an error so that the XML is retained instead of silently losing content.
"""
import hashlib
import html
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FONT_LOCK = threading.Lock()
NATIVE_VERSION = "native-1"


def register_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab
    import ziafont
    with FONT_LOCK:
        if 'JD' not in pdfmetrics.getRegisteredFontNames():
            root = Path(reportlab.__file__).parent / 'fonts'
            for name, file in [('JD', 'Vera.ttf'), ('JD-Bold', 'VeraBd.ttf'),
                               ('JD-Italic', 'VeraIt.ttf'), ('JD-BoldItalic', 'VeraBI.ttf')]:
                pdfmetrics.registerFont(TTFont(name, str(root / file)))
            pdfmetrics.registerFontFamily('JD', normal='JD', bold='JD-Bold', italic='JD-Italic', boldItalic='JD-BoldItalic')
            pdfmetrics.registerFont(TTFont('JD-Extra', str(Path(ziafont.__file__).parent / 'fonts/DejaVuSans.ttf')))
            # Windows normally supplies CJK fonts. They are embedded as subsets
            # at runtime, never copied into the application distribution.
            cjk = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
            if cjk.is_file():
                pdfmetrics.registerFont(TTFont('JD-CJK', str(cjk), subfontIndex=0))


class NativeRenderer:
    def __init__(self, control, emit=lambda **event: None):
        self.control, self.emit = control, emit

    def ensure_ready(self):
        self.control.checkpoint()
        register_fonts()

    def render(self, document, doi):
        self.ensure_ready()
        with tempfile.TemporaryDirectory(prefix='journal-reading-') as directory:
            return ReadingDocument(Path(directory), self.control).build(document, doi)


class ReadingDocument:
    def __init__(self, work, control):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
        self.work, self.control = work, control
        self.width = 495.28  # A4 minus 50 pt margins on each side.
        base = dict(fontName='JD', fontSize=9.5, leading=13.5, spaceAfter=6,
                    alignment=TA_JUSTIFY, splitLongWords=True, allowWidows=0, allowOrphans=0)
        self.styles = {'body': ParagraphStyle('body', **base)}
        for name, changes in {
            'title': dict(fontName='JD-Bold', fontSize=17, leading=21, spaceBefore=8, spaceAfter=13, alignment=TA_LEFT),
            'heading': dict(fontName='JD-Bold', fontSize=10.5, leading=14, spaceBefore=11, spaceAfter=6, keepWithNext=True, alignment=TA_LEFT),
            'small': dict(fontSize=8, leading=11, alignment=TA_LEFT),
            'caption': dict(fontSize=8.5, leading=11.5, alignment=TA_LEFT),
            'cell': dict(fontSize=7.5, leading=10.5, spaceAfter=0, alignment=TA_LEFT),
            'journal': dict(fontName='JD-Bold', fontSize=14, leading=18, spaceAfter=8, alignment=TA_LEFT),
        }.items():
            self.styles[name] = ParagraphStyle(name, **{**base, **changes})
        self.math_cache = {}

    def safe_text(self, value):
        from reportlab.pdfbase import pdfmetrics
        fonts = [name for name in ('JD', 'JD-Extra', 'JD-CJK') if name in pdfmetrics.getRegisteredFontNames()]
        result = []
        for char in value or '':
            if char.isspace():
                result.append(' ')
                continue
            if char in '\u200b\u00ad\ufeff':
                continue
            selected = next((name for name in fonts if ord(char) in pdfmetrics.getFont(name).face.charToGlyph), None)
            if selected is None:
                raise ValueError(f'内置字体不能显示字符 U+{ord(char):04X}，XML 已保留；可尝试浏览器排版。')
            value = html.escape(char, quote=False)
            result.append(value if selected == 'JD' else f'<font name="{selected}">{value}</font>')
        return ''.join(result)

    def image_path(self, node):
        src = urllib.parse.urlsplit(node.get('src', ''))
        if src.scheme != 'file' or src.netloc:
            raise ValueError('内置排版仅允许本地已校验的图片。')
        return Path(urllib.request.url2pathname(src.path))

    def math(self, node, width):
        from lxml import etree
        import ziamath
        import ziafont
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        raw = etree.tostring(node, encoding='unicode', with_tail=False)
        key = hashlib.sha256(raw.encode()).hexdigest()
        if key not in self.math_cache:
            root = ET.fromstring(raw)
            for item in root.iter():
                item.tag = item.tag.rsplit('}', 1)[-1]
            # ziamath logs unsupported tags and substitutes a generic row;
            # reject them explicitly rather than accept a distorted equation.
            from ziamath.nodes.mnode import _node_classes
            for item in root.iter():
                if item.tag == 'mstyle':
                    item.tag = 'mrow'
                if item.tag == 'semantics':
                    item.tag = 'mrow'
                    for child in list(item):
                        if child.tag.startswith('annotation'):
                            item.remove(child)
                if item.tag not in _node_classes and item.tag not in ('math', 'mtd', 'mtr', 'none', 'ms', 'mprescripts'):
                    raise ValueError(f'内置排版暂不支持公式节点 {item.tag}，XML 已保留；可尝试浏览器排版。')
            # svglib consumes SVG 1.1 paths; SVG 2 symbol/use would lose glyphs.
            ziafont.config.svg2 = False
            svg = ziamath.Math(root, size=11).svg()
            drawing = svg2rlg(io.BytesIO(svg.encode()))
            if drawing is None or drawing.width <= 0 or drawing.height <= 0:
                raise ValueError('公式无法完整排版，XML 已保留。')
            path = self.work / (key + '.png')
            path.write_bytes(renderPM.drawToString(drawing, fmt='PNG', dpi=216))
            self.math_cache[key] = path, drawing.width, drawing.height
        path, w, h = self.math_cache[key]
        scale = min(1, max(20, width - 12) / w)
        if scale < .55:
            raise ValueError('公式过宽，内置排版会导致难以阅读，XML 已保留；可尝试浏览器排版。')
        return f'<img src="{html.escape(str(path), quote=True)}" width="{w*scale:.3f}" height="{h*scale:.3f}" valign="middle"/>'

    def inner(self, node, width):
        return self.safe_text(node.text) + ''.join(self.inline(c, width) + self.safe_text(c.tail) for c in node)

    def inline(self, node, width):
        from PIL import Image
        tag = node.tag.lower()
        if tag == 'math':
            return self.math(node, width)
        if tag == 'br':
            return '<br/>'
        if tag == 'img':
            path = self.image_path(node)
            with Image.open(path) as image:
                w, h = image.size
            match = re.search(r'height:([.\d]+)em', node.get('style', ''))
            height = float(match[1]) * 9.5 if match else 10
            scale = min(height / h, max(20, width - 12) / w)
            return f'<img src="{html.escape(str(path), quote=True)}" width="{w*scale:.3f}" height="{h*scale:.3f}" valign="middle"/>'
        content = self.inner(node, width)
        if tag in ('b', 'i', 'u', 'sup', 'sub'):
            return f'<{tag}>{content}</{tag}>'
        if tag in ('script', 'style'):
            return ''
        return content + ('<br/>' if tag in ('p', 'div', 'li', 'h2', 'h3', 'h4') else '')

    def paragraph(self, node, style='body', width=None):
        from reportlab.platypus import Paragraph
        return Paragraph(self.inner(node, width or self.width), self.styles[style])

    def figure(self, node):
        from reportlab.platypus import Image, KeepTogether, Spacer
        output = []
        for image in node.findall('.//img'):
            item = Image(str(self.image_path(image)))
            scale = min(1, self.width / item.imageWidth, 340 / item.imageHeight)
            item.drawWidth, item.drawHeight = item.imageWidth * scale, item.imageHeight * scale
            output += [item, Spacer(1, 5)]
        caption = node.find('figcaption')
        if caption is not None:
            output.append(self.paragraph(caption, 'caption'))
        return [KeepTogether(output), Spacer(1, 8)]

    def table(self, node):
        from reportlab.platypus import LongTable, TableStyle
        from reportlab.lib import colors
        rows, spans, occupied, header_rows = [], [], {}, 0
        for row in node.findall('.//tr'):
            self.control.checkpoint()
            y, x, cells = len(rows), 0, []
            for cell in row:
                if cell.tag not in ('td', 'th'):
                    continue
                while (x, y) in occupied:
                    cells.append('')
                    x += 1
                colspan, rowspan = int(cell.get('colspan', '1')), int(cell.get('rowspan', '1'))
                if not 1 <= colspan <= 100 or not 1 <= rowspan <= 1000:
                    raise ValueError('表格跨行/跨列结构无效。')
                cells.append((cell, colspan))
                cells.extend([''] * (colspan - 1))
                if colspan > 1 or rowspan > 1:
                    spans.append(('SPAN', (x, y), (x + colspan - 1, y + rowspan - 1)))
                for xx in range(x, x + colspan):
                    for yy in range(y + 1, y + rowspan):
                        occupied[(xx, yy)] = True
                x += colspan
            rows.append(cells)
            if row.getparent().tag == 'thead':
                header_rows += 1
        if not rows:
            return []
        columns = max(max(len(row) for row in rows), max((x + 1 for x, _ in occupied), default=0))
        if columns > 18 or any(span[2][1] >= len(rows) for span in spans):
            raise ValueError('表格过宽或跨行超出范围，XML 已保留；可尝试浏览器排版。')
        col_width = self.width / columns
        data = [[self.paragraph(cell[0], 'cell', col_width * cell[1] - 10) if isinstance(cell, tuple) else cell
                 for cell in row] + [''] * (columns - len(row)) for row in rows]
        table = LongTable(data, colWidths=[col_width] * columns, repeatRows=header_rows,
                          splitByRow=1, splitInRow=1, hAlign='LEFT')
        table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEABOVE', (0, 0), (-1, 0), .7, colors.HexColor('#405968')),
            ('LINEBELOW', (0, -1), (-1, -1), .5, colors.HexColor('#405968')),
            ('ROWBACKGROUNDS', (0, header_rows), (-1, -1), [colors.white, colors.HexColor('#F2F5F6')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)] + spans))
        caption = node.find('caption')
        output = [self.paragraph(caption, 'caption')] if caption is not None else []
        if output:
            output[0].keepWithNext = True
        return output + [table]

    def blocks(self, node):
        from reportlab.platypus import Paragraph, Spacer, HRFlowable
        result = []
        if (node.text or '').strip():
            result.append(Paragraph(self.safe_text(node.text), self.styles['body']))
        for child in node:
            self.control.checkpoint()
            tag, cls = child.tag.lower(), child.get('class', '')
            if tag in ('script', 'style'):
                continue
            if tag == 'h1':
                result.append(self.paragraph(child, 'title'))
            elif tag in ('h2', 'h3', 'h4'):
                result.append(self.paragraph(child, 'heading'))
            elif tag == 'figure':
                result.extend(self.figure(child))
            elif tag == 'aside':
                result.append(self.paragraph(child, 'small'))
                result.append(Spacer(1, 8))
            elif tag == 'table':
                result.extend(self.table(child))
                result.append(Spacer(1, 9))
            elif tag in ('p', 'li'):
                result.append(self.paragraph(child, 'small' if cls == 'reference' else 'body'))
            elif tag == 'math' or cls == 'equation':
                value = self.math(child, self.width) if tag == 'math' else self.inner(child, self.width)
                result.append(Paragraph(value, self.styles['body']))
            elif cls in ('authors', 'affiliations', 'citation', 'edition', 'doi', 'copyright', 'table-note', 'journal'):
                result.append(self.paragraph(child, 'journal' if cls == 'journal' else 'small'))
            else:
                result.extend(self.blocks(child))
                if tag in ('header', 'aside') or cls == 'front':
                    result += [Spacer(1, 6), HRFlowable(width='100%', thickness=.5, color='#9CABB2'), Spacer(1, 8)]
            if (child.tail or '').strip():
                result.append(Paragraph(self.safe_text(child.tail), self.styles['body']))
        return result

    def build(self, document, doi):
        from lxml import html as parser
        from reportlab.platypus import SimpleDocTemplate
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        root = parser.fromstring(document)
        story = self.blocks(root.find('body'))
        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=50, rightMargin=50,
            topMargin=42, bottomMargin=42, title=root.findtext('head/title') or doi,
            author='XML reading edition', pageCompression=1)
        def page(canvas, document):
            self.control.checkpoint()
            canvas.saveState()
            canvas.setFont('JD', 7)
            canvas.setFillColor(colors.HexColor('#60727D'))
            canvas.drawString(50, 24, doi + ' | XML reading edition')
            canvas.drawRightString(A4[0] - 50, 24, str(document.page))
            canvas.restoreState()
        doc.build(story, onFirstPage=page, onLaterPages=page)
        self.control.checkpoint()
        return output.getvalue()
