"""Authenticated figure retrieval. Credentials never enter URLs or cache files."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

from engine import (ActionRequired, NETWORK_ERRORS, atomic_bytes, elsevier_opener,
                    load_json, quota_deadline, retry_seconds, save_json)
from xml_utils import local

MAX_IMAGE_BYTES = 64 * 1024 * 1024


class MissingImage(Exception):
    """This article remains pending; do not label its PDF complete."""


def object_url(value):
    """Rebuild a narrowly scoped API URL; discard arbitrary query parameters."""
    parsed = urllib.parse.urlsplit(value.strip())
    if (parsed.scheme != "https" or parsed.hostname != "api.elsevier.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or not re.fullmatch(r"/content/object/(?:eid|pii)/[A-Za-z0-9._-]+", parsed.path)):
        raise MissingImage("XML 中的图片地址不属于受支持的 Elsevier Object API。")
    return "https://api.elsevier.com" + parsed.path + "?httpAccept=%2A%2F%2A"


def image_details(data):
    with Image.open(io.BytesIO(data)) as picture:
        picture.verify()
    with Image.open(io.BytesIO(data)) as picture:
        picture.load()
        if picture.width < 1 or picture.height < 1:
            raise ValueError("图片尺寸无效")
        return {"width": picture.width, "height": picture.height, "format": picture.format,
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def image_objects(root):
    found = {}
    quality = {"IMAGE-HIGH-RES": 0, "IMAGE-DOWNSAMPLED": 1, "IMAGE": 2}
    for node in root.iter():
        if local(node) != "object" or node.get("type") not in quality:
            continue
        ref = node.get("ref", "")
        if not ref:
            continue
        found.setdefault(ref, []).append({"url": object_url(node.text or ""),
                                          "type": node.get("type")})
    for items in found.values():
        items.sort(key=lambda item: quality[item["type"]])
    return found


class ImageCache:
    def __init__(self, directory, api_key, network, control, emit):
        self.directory = Path(directory)
        self.key = api_key
        self.control = control
        self.emit = emit
        self.opener = elsevier_opener(network)
        self.last_request = 0.0

    def get(self, ref, candidates):
        if not candidates:
            raise MissingImage(f"图片 {ref} 没有可用的图片对象地址。")
        # Scan all cached variants first: an earlier ordinary-image fallback also works offline.
        for candidate in candidates:
            self.control.checkpoint()
            url = object_url(candidate["url"])
            name = hashlib.sha256(url.encode()).hexdigest()[:24]
            target = self.directory / (name + ".image")
            meta_path = target.with_suffix(".json")
            cached = load_json(meta_path, {})
            try:
                data = target.read_bytes()
                info = image_details(data)
                if (cached.get("url") == url and cached.get("sha256") == info["sha256"]
                        and cached.get("bytes") == len(data)):
                    return target, {**info, "ref": ref, "quality": candidate["type"]}
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        for candidate in candidates:
            self.control.checkpoint()
            url = object_url(candidate["url"])
            name = hashlib.sha256(url.encode()).hexdigest()[:24]
            target = self.directory / (name + ".image")
            meta_path = target.with_suffix(".json")
            if not self.key:
                raise ActionRequired("缺少图片缓存。请在主界面填写 API Key 后继续合成。")
            self.emit(kind="phase", text=f"下载图片 {ref}（{'高清' if candidate['type'] == 'IMAGE-HIGH-RES' else '普通'}）…")
            data = self.fetch(url)
            if data is None:  # Only a definite missing object permits lower quality fallback.
                continue
            try:
                info = image_details(data)
            except (OSError, ValueError, Image.DecompressionBombError):
                raise MissingImage(f"图片 {ref} 的响应无法解码，已保留为待处理。") from None
            atomic_bytes(target, data)
            save_json(meta_path, {**info, "url": url, "quality": candidate["type"]})
            return target, {**info, "ref": ref, "quality": candidate["type"]}
        raise MissingImage(f"图片 {ref} 的高清和普通对象均不可用。")

    def fetch(self, url):
        failures = 0
        while failures < 4:
            self.control.checkpoint()
            self.control.wait(max(0, 1.5 - (time.monotonic() - self.last_request)))
            request = urllib.request.Request(object_url(url), headers={
                "X-ELS-APIKey": self.key, "Accept": "*/*", "Accept-Encoding": "identity"})
            self.last_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=30) as response:
                    pieces, size = [], 0
                    started = last_notice = time.monotonic()
                    while True:
                        self.control.checkpoint()
                        if time.monotonic() - started > 600:
                            raise TimeoutError("图片传输超时")
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES:
                            raise MissingImage("图片超过 64 MB，需单独检查。")
                        pieces.append(chunk)
                        if time.monotonic() - last_notice >= 5:
                            self.emit(kind="phase", text=f"正在接收图片 · {size / 1024 / 1024:.1f} MB")
                            last_notice = time.monotonic()
                    data = b"".join(pieces)
                    length = response.headers.get("Content-Length")
                    if length and length.isdigit() and int(length) != len(data):
                        raise OSError("图片响应不完整")
                    return data
            except urllib.error.HTTPError as error:
                code, headers = error.code, error.headers
                error.close()
                if code in (401, 403):
                    raise ActionRequired(f"图片 API 返回 HTTP {code}。请检查 API Key、学校权限和网络出口；已缓存的图片会保留。") from None
                if code in (404, 410):
                    return None
                if code == 429:
                    reset = quota_deadline(headers.get("X-RateLimit-Reset"))
                    delay = max(retry_seconds(headers.get("Retry-After")) or 60,
                                reset - time.time() if reset else 0)
                    self.control.wait(delay, "图片 API 限流")
                    continue
                if code not in (408, 425, 500, 502, 503, 504):
                    raise MissingImage(f"图片 API 返回 HTTP {code}。") from None
                failures += 1
                delay = retry_seconds(headers.get("Retry-After")) or 5 * 2 ** failures
            except NETWORK_ERRORS:
                failures += 1
                delay = 5 * 2 ** failures
            if failures < 4:
                self.control.wait(delay, f"图片连接暂时失败，重试 {failures}/3")
        raise MissingImage("图片连续连接失败，稍后再次合成可从已缓存位置继续。")
