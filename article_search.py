"""ScienceDirect Search V2: explicit queries, isolated caches and bounded paging."""
from collections import Counter
from datetime import date
import hashlib
import html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

from article_filter import phrases

SCOPES = {"全文 · ScienceDirect": "fulltext", "题名 · ScienceDirect": "title", "本地题名筛选": "local"}
SORTS = {"相关度优先": "relevance", "最新发表优先": "date"}
ENDPOINT = "https://api.elsevier.com/content/search/sciencedirect"
PAGE_SIZE = 100
MAX_RESULTS = 6000
SCHEMA = 2


def uses_api(scope, query):
    return scope != "local" and bool(query.strip())


def expression(query, mode):
    query = query.strip()
    if not query:
        return ""
    if mode == "native":
        # The publisher interprets operators and linguistic matching. We only
        # check obvious input mistakes, without rewriting its query language.
        query = query.translate(str.maketrans({'“': '"', '”': '"'}))
        quoted, depth = False, 0
        for char in query:
            if char == '"':
                quoted = not quoted
            elif not quoted and char == '(':
                depth += 1
            elif not quoted and char == ')':
                depth -= 1
                if depth < 0:
                    raise ValueError("检索式的括号不成对。")
        if quoted or depth:
            raise ValueError("检索式的双引号或括号不成对。")
        value = query
    elif mode in ("any", "all"):
        terms = phrases(query)
        if not terms:
            raise ValueError("请填写有效关键词，多个词组用逗号分隔。")
        value = (" OR " if mode == "any" else " AND ").join('"' + term + '"' for term in terms)
    else:
        raise ValueError("检索语法无效。")
    if len(value) > 250:
        raise ValueError("ScienceDirect 单个检索字段最多 250 个字符（含生成的引号与运算符）。")
    if any(ord(char) < 32 for char in value):
        raise ValueError("检索式不能包含换行或控制字符。")
    return value


def validate_search(query, mode, scope, sort):
    if scope not in SCOPES.values() or sort not in SORTS.values():
        raise ValueError("检索范围或排序无效。")
    if scope == "local":
        if mode == "native":
            raise ValueError("本地题名筛选请选择「任一词组」或「全部词组」；官网语法需使用 ScienceDirect 检索。")
        from article_filter import filter_records
        filter_records([], query, mode)
    else:
        expression(query, mode)


def search_spec(journal, start, end, query, mode, scope, sort):
    publication = '"' + journal.name.replace('"', ' ') + '"'
    if len(publication) > 250:
        raise ValueError("期刊名称超过 ScienceDirect 搜索字段长度限制。")
    # Unquoted pub names are token searches and can include other journals.
    body = {"pub": publication, "date": f"{start}-{end}", "display": {"sortBy": sort}}
    value = expression(query, mode)
    if value:
        body["title" if scope == "title" else "qs"] = value
    return {"schema": SCHEMA, "issn": journal.issn, "scope": scope, "body": body}


def cache_path(state, spec):
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return state / "searches" / (digest + ".json")


def cached_search(state, spec):
    from engine import load_json
    cached = load_json(cache_path(state, spec), {})
    if not isinstance(cached, dict) or cached.get("spec") != spec or cached.get("complete") is not True:
        return None
    rows = cached.get("records")
    if (type(cached.get("results_found")) is not int or cached["results_found"] < 0 or not isinstance(cached.get("created_at"), str)
            or cached.get('unique_received') != cached['results_found'] or cached.get('missing') != 0
            or not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("doi"), str)
            and isinstance(row.get("year"), int) and isinstance(row.get("date"), str)
            and isinstance(row.get("title"), str) for row in rows)
            or len(rows) > cached["results_found"] or len({row["doi"] for row in rows}) != len(rows)):
        return None
    return cached


def website_url(journal, start, end, query, mode, scope, sort):
    params = search_spec(journal, start, end, query, mode, scope, sort)["body"].copy()
    params.pop("display")
    # The website rejects quoted publication titles; its ISSN field provides
    # the exact journal restriction that PUT's quoted pub expression provides.
    params["pub"] = journal.name
    params["docId"] = journal.issn
    params["sortBy"] = sort
    return "https://www.sciencedirect.com/search?" + urllib.parse.urlencode(params)


def request_page(runner, body):
    from engine import AGENT, ActionRequired, NETWORK_ERRORS, retry_seconds
    for attempt in range(4):
        runner.control.checkpoint()
        request = urllib.request.Request(ENDPOINT, method="PUT",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"X-ELS-APIKey": runner.key, "Content-Type": "application/json",
                     "Accept": "application/json", "Accept-Encoding": "identity",
                     "User-Agent": AGENT, "X-ELS-ReqId": str(uuid.uuid4())})
        delay = 0
        try:
            with runner.opener.open(request, timeout=runner.config.timeout) as response:
                payload = runner.read_response(response)
            if runner.key and runner.key.encode() in payload:
                raise ActionRequired("搜索响应含有凭据，已停止且未保存该响应。")
            try:
                data = json.loads(payload)
            except ValueError:
                raise ActionRequired("ScienceDirect 搜索未返回有效 JSON，请检查网络后重试。") from None
            if not isinstance(data, dict) or type(data.get("resultsFound")) is not int or not isinstance(data.get("results"), list):
                raise ActionRequired("ScienceDirect 搜索响应结构不完整；未将其当作零结果。")
            if data["resultsFound"] < 0:
                raise ActionRequired("ScienceDirect 搜索返回无效的结果数量。")
            return data
        except urllib.error.HTTPError as error:
            code, headers = error.code, error.headers
            error.close()
            if code in (401, 403):
                raise ActionRequired(f"ScienceDirect 搜索 HTTP {code}：当前 Key 或机构出口未获搜索接口授权。"
                    "全文下载权限不代表搜索权限；请检查校园网/VPN及 Key 授权，或手动选择「本地题名筛选」。"
                    "也可用「官网」核对检索条件；不会自动缩小为题名检索。") from None
            if code == 400:
                raise ActionRequired("ScienceDirect 拒绝此检索式（HTTP 400）。请检查大写 AND / OR / NOT、"
                    "英文双引号和括号，或改用任一/全部词组。") from None
            if code == 429 and headers.get("X-RateLimit-Remaining") == "0":
                raise ActionRequired("ScienceDirect 搜索配额已用完；请在配额恢复后重试，已完成的查询缓存可继续复用。") from None
            if code not in (408, 429, 500, 502, 503, 504):
                raise ActionRequired(f"ScienceDirect 搜索返回 HTTP {code}，请检查网络或接口状态。") from None
            delay = retry_seconds(headers.get("Retry-After")) or (60 if code == 429 else 0)
        except NETWORK_ERRORS as error:
            if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
                raise ActionRequired("ScienceDirect 搜索证书校验失败，请检查系统时间与网络证书。") from None
        if attempt == 3 or delay > 300:
            raise ActionRequired("ScienceDirect 搜索暂时不可用或要求较长等待，请稍后重试；未开始下载全文。")
        runner.control.wait(max(delay, 2 ** (attempt + 1)), "搜索请求暂时失败，等待重试")


def collect_search(runner):
    from engine import normalize_title, save_json, timestamp
    cfg, journal = runner.config, runner.journal
    spec = search_spec(journal, cfg.start_year, cfg.end_year, cfg.title_filter,
                       cfg.filter_mode, cfg.search_scope, cfg.search_sort)
    cached = cached_search(runner.state, spec)
    if cached and not cfg.refresh:
        runner.log("检索范围：" + scope_description(cfg.search_scope))
        runner.log(f"复用 ScienceDirect 检索结果：{len(cached['records'])} 条（{cached['created_at']}）。更新论文列表可重新检索。")
        return cached
    runner.log("ScienceDirect API 检索：" + ("论文题名" if cfg.search_scope == "title" else "全部内容（不含参考文献）") +
               "；按" + ("相关度" if cfg.search_sort == "relevance" else "最新发表日期") + "排序。")
    runner.log("检索式：" + (spec["body"].get("qs", spec["body"].get("title")) or "留空（所选期刊与年份）"))
    allowed = {normalize_title(journal.name)}
    from journal_catalog import lookup
    item = lookup(journal.slug, journal.name)
    if item:
        allowed.update(normalize_title(alias) for alias in item.get("aliases", []))
    from search_paging import collect_pages
    if cfg.refresh:
        cache_path(runner.state, spec).unlink(missing_ok=True)
    paging = collect_pages(runner, spec)
    rows, dropped = [], Counter()
    for entry in paging['raw_records']:
        doi = str(entry.get("doi") or "").strip().lower()
        source = str(entry.get("sourceTitle") or "")
        if normalize_title(source) not in allowed:
            dropped["其他期刊或期刊不明"] += 1
            continue
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            dropped["缺少有效 DOI"] += 1
            continue
        day = str(entry.get("publicationDate") or "")[:10]
        try:
            year = date.fromisoformat(day).year
        except ValueError:
            dropped["发表日期不明"] += 1
            continue
        if not cfg.start_year <= year <= cfg.end_year:
            dropped["年份范围外"] += 1
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", str(entry.get("title") or "")))
        rows.append({"doi": doi, "title": title, "date": day,
                     "year": year, "pii": str(entry.get("pii") or ""), "journal": source})
    unique, dois = [], set()
    for row in rows:
        if row["doi"] not in dois:
            unique.append(row)
            dois.add(row["doi"])
    if len(unique) != len(rows):
        dropped["重复 DOI"] += len(rows) - len(unique)
    result = {key: value for key, value in paging.items() if key != 'raw_records'}
    result.update(spec=spec, created_at=timestamp(), excluded=dict(dropped), records=unique)
    result['notice'] = ''
    if not result['complete']:
        gap = f"尚差 {result['missing']} 条" if result['missing'] else '翻页期间数量发生变化，覆盖范围仍待复核'
        result['notice'] = (f"检索列表尚未完整：接口报告 {result['results_found']} 条，去重已获取 {result['unique_received']} 条，{gap}。"
                            f"本次可先处理其中已确认匹配的 {len(unique)} 篇；再次开始会自动补查，更新论文列表可从头检索。")
    save_json(cache_path(runner.state, spec), result)
    if result['complete']:
        cache_path(runner.state, spec).with_suffix('.partial.json').unlink(missing_ok=True)
    runner.log(f"ScienceDirect 接口报告 {result['results_found']} 条，去重获取 {result['unique_received']} 条，可处理 {len(unique)} 条。" +
               ("排除：" + "；".join(f"{key} {value} 条" for key, value in dropped.items()) if dropped else ""))
    if result['notice']:
        runner.log(result['notice'])
    return result


def scope_description(scope):
    return {"fulltext": "全文（不含参考文献）", "title": "仅论文标题，不检索摘要或正文",
            "local": "仅本地候选题名，不检索摘要或正文"}[scope]
