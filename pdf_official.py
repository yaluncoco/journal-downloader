"""Official PDF retrieval with XML page-count evidence, never preview acceptance."""
from types import SimpleNamespace
import time
import xml.etree.ElementTree as ET

from article_transfer import fetch_article
from engine import ActionRequired, Downloader, elsevier_opener

METHODS = {"自动：官方优先 / 内置单栏": "auto", "内置单栏（无需浏览器）": "native", "浏览器排版（Edge / Chrome）": "browser"}


def pdf_facts(data):
    root = ET.fromstring(data)
    namespace = '{http://www.elsevier.com/xml/xocs/dtd}'
    counts = {(n.text or '').strip() for n in root.iter(namespace + 'web-pdf-page-count')}
    pages = int(next(iter(counts))) if len(counts) == 1 and next(iter(counts)).isdigit() else None
    core = next((n for n in root.iter() if n.tag.rsplit('}', 1)[-1] == 'coredata'), None)
    values = {n.tag.rsplit('}', 1)[-1]: (n.text or '').strip() for n in core} if core is not None else {}
    return {'expected_pages': pages if pages and 0 < pages <= 10000 else None,
            'open_access': values.get('openaccess') == '1' or values.get('openaccessArticle') == 'true'}


class OfficialPdfClient:
    def __init__(self, key, network, control, emit):
        self.key, self.control, self.emit = key, control, emit
        self.opener = elsevier_opener(network)
        self.config = SimpleNamespace(timeout=45)
        self.skip_subscription = False
        self.disabled = False
        self.exhausted = False
        self.next_request = 0

    read_response = Downloader.read_response
    scrub = Downloader.scrub

    def get(self, doi, facts):
        """Return complete bytes or a reason to use the authorized XML instead."""
        if not self.key or not facts['expected_pages']:
            return None, '未提供 Key 或 XML 缺少官方页数，使用内置排版。'
        if self.disabled or (self.skip_subscription and not facts['open_access']):
            return None, '本次会话的官方 PDF 权限不可用，使用内置排版。'
        if self.exhausted:
            raise ActionRequired('官方 PDF 配额已用完；已保存上次成功文件，请稍后继续或选择「内置单栏」。')
        self.control.wait(max(0, self.next_request - time.monotonic()))
        data, info = fetch_article(self, doi, 'application/pdf')
        self.next_request = time.monotonic() + 3
        self.exhausted = info.get('remaining') == '0'
        if info.get('wait_until') or info.get('status') == 'quota_exhausted' or (self.exhausted and data is None):
            raise ActionRequired('官方 PDF 接口限流或配额不足；已保留 XML，请配额恢复后重试，或选择「内置单栏」继续。')
        if info.get('status') == 'authentication_error':
            self.disabled = True
            return None, '当前 API 未获官方 PDF 授权；使用已授权 XML 内置排版。网页登录会话不会自动用于 API。'
        if info.get('status') == 'certificate_error':
            raise ActionRequired(info['action_required'])
        if data is None:
            return None, f"官方 PDF 暂不可用（{info.get('status', 'unknown')}），使用内置排版。"
        try:
            from pdf_export import validate_pdf
            pages = validate_pdf(data, doi)
        except Exception:
            return None, '官方响应未通过 PDF / DOI 校验，使用内置排版。'
        if pages != facts['expected_pages']:
            if self.exhausted:
                raise ActionRequired('官方 PDF 返回不完整预览且配额归零，XML 已保留；请稍后继续或选择「内置单栏」。')
            if not facts['open_access']:
                self.skip_subscription = True
            return None, f"官方 PDF 仅返回 {pages}/{facts['expected_pages']} 页，未作为全文保存；使用内置排版。"
        return data, ''
