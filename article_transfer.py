"""Shared, bounded Article Retrieval transport for XML and official PDFs."""
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

def fetch_article(self, doi, accept="text/xml"):
    from engine import AGENT, ActionRequired, NETWORK_ERRORS, quota_deadline, retry_seconds
    url = "https://api.elsevier.com/content/article/doi/" + urllib.parse.quote(doi, safe="/") + "?view=FULL"
    for attempt in range(6):
        self.control.checkpoint()
        headers = {"X-ELS-APIKey": self.key, "Accept": accept, "User-Agent": AGENT,
                   "X-ELS-ReqId": str(uuid.uuid4()), "Accept-Encoding": "identity"}
        info = {"attempts": attempt + 1}
        try:
            with self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.config.timeout) as response:
                data = self.read_response(response)
                info.update(http=response.status, remaining=response.headers.get("X-RateLimit-Remaining"),
                            quota_reset_epoch=response.headers.get("X-RateLimit-Reset"), bytes=len(data))
            if self.key.encode() in data:
                raise ActionRequired("API 响应中含有凭据，已停止且未保存该响应。")
            return data, info
        except urllib.error.HTTPError as error:
            code, response_headers = error.code, error.headers
            try:
                message = error.read(3000).decode("utf-8", "replace")
            except NETWORK_ERRORS:
                message = ""
            finally:
                error.close()
            info.update(http=code, message=self.scrub(message)[:800])
            if code in (401, 403):
                return None, {**info, "status": "authentication_error", "action_required":
                    f"HTTP {code}：API Key 或机构权限未被认可。请确认校园网/VPN，并保持「直连 Elsevier」；TUN 全局代理仍可能影响出口。修复后再开始，已有文件会保留。"}
            quota = (response_headers.get("X-RateLimit-Remaining") == "0" or
                     "QUOTA" in (message + response_headers.get("X-ELS-Status", "")).upper())
            if code == 429 and quota:
                deadline = quota_deadline(response_headers.get("X-RateLimit-Reset"))
                if not deadline:
                    return None, {**info, "status": "quota_exhausted", "action_required": "API 配额已用完，服务器未给出有效恢复时间。请配额恢复后再继续。"}
                return None, {**info, "status": "quota_wait", "wait_until": deadline}
            required = retry_seconds(response_headers.get("Retry-After"))
            if code not in (408, 429, 500, 502, 503, 504):
                return None, {**info, "status": "http_error"}
            info["status"] = "network_error" if code == 408 else "server_error"
            if code == 429 or required:
                return None, {**info, "status": "server_wait", "wait_until": time.time() + max(60 if code == 429 else 2, required or 0)}
        except NETWORK_ERRORS as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, ssl.SSLCertVerificationError):
                return None, {**info, "status": "certificate_error", "action_required": "HTTPS 证书校验失败，请检查系统时间或校园网证书配置。"}
            info.update(status="network_error", message=self.scrub(str(reason))[:600])
        if attempt == 5:
            return None, info
        self.control.wait(min(60, 2 ** (attempt + 1)), f"连接暂时失败，重试 {attempt + 1}/5")
