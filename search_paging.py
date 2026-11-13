"""Bounded recovery for a live offset-based search index, with honest coverage."""
import hashlib
import json
import math

MAX_SWEEPS = 3


def identity(entry):
    for field in ('pii', 'doi', 'uri'):
        value = str(entry.get(field) or '').strip().lower()
        if value:
            return field + ':' + value
    return 'record:' + hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def collect_pages(runner, spec):
    from article_search import request_page, cache_path, PAGE_SIZE, MAX_RESULTS
    from engine import ActionRequired, load_json, save_json, timestamp
    checkpoint = cache_path(runner.state, spec).with_suffix('.partial.json')
    saved = load_json(checkpoint, {})
    valid = (isinstance(saved, dict) and saved.get('spec') == spec and saved.get('complete') is False
             and type(saved.get('reported_total')) is int and 0 <= saved['reported_total'] <= MAX_RESULTS
             and isinstance(saved.get('raw_records'), list) and len(saved['raw_records']) <= MAX_SWEEPS * MAX_RESULTS
             and all(isinstance(row, dict) for row in saved['raw_records']))
    records = {identity(row): row for row in saved['raw_records']} if valid else {}
    totals = {saved['reported_total']} if valid else set()
    changed = bool(saved.get('total_changed')) if valid else False
    total, requests, duplicates, complete = (saved['reported_total'] if valid else 0), 0, 0, False
    if records:
        runner.log(f'读取未完成检索进度：已有 {len(records)} 条，继续补查；已下载文件仍按完整性校验跳过。')
    requested_sort = spec['body']['display']['sortBy']
    catalog = 'issn' in spec['body']
    sorts = (requested_sort,) * MAX_SWEEPS if catalog else (requested_sort, 'date' if requested_sort == 'relevance' else 'relevance', requested_sort)

    def persist():
        save_json(checkpoint, {'spec': spec, 'complete': False, 'updated_at': timestamp(),
            'reported_total': total, 'total_changed': changed, 'raw_records': list(records.values())})

    for sweep, sort in enumerate(sorts):
        if sweep:
            runner.log(f'自动补查 {sweep}/{MAX_SWEEPS-1}：重新读取官方目录，合并缺项。' if catalog else
                       f'自动补查 {sweep}/{MAX_SWEEPS-1}：保持关键词、期刊与年份，改用'
                       f'{"相关度" if sort == "relevance" else "发表日期"}顺序取回缺项；补回记录追加到列表末尾。')
        before, offset, pages = len(records), 0, 0
        pass_records, pass_totals, page_signatures = {}, set(), set()
        while True:
            runner.control.checkpoint()
            if requests:
                runner.control.wait(max(1, runner.config.interval), '检索分页间隔')
            body = {**spec['body'], 'display': {'sortBy': sort, 'offset': offset, 'show': PAGE_SIZE}}
            data = request_page(runner, body)
            requests += 1
            pages += 1
            total, items = data['resultsFound'], data['results']
            if total > MAX_RESULTS:
                raise ActionRequired(f'此检索命中 {total} 条，超过本软件单次完整获取上限 {MAX_RESULTS} 条。'
                                     '请缩小年份或增加关键词后重试；未截断结果，也未开始下载。')
            if len(items) > PAGE_SIZE or any(not isinstance(row, dict) for row in items):
                raise ActionRequired('ScienceDirect 返回无效分页记录，已停止；此前检索进度已保留。')
            totals.add(total)
            pass_totals.add(total)
            changed = changed or len(totals) > 1
            keys = [identity(row) for row in items]
            overlap = len(keys) - len(set(keys).difference(records))
            duplicates += overlap
            for key, row in zip(keys, items):
                records[key] = row  # Updating metadata preserves first-seen order.
                pass_records[key] = row
            offset += len(items)  # Offset counts raw hits, never deduplicated hits.
            reached_end = offset >= total
            # A full stable sweep supersedes older entries if the index changed.
            if reached_end and len(pass_totals) == 1 and len(pass_records) == total:
                if changed or len(records) != total:
                    records = {key: pass_records[key] for key in records if key in pass_records}
                    runner.log('已使用本轮完整结果复核变动索引，保留有效记录的首次出现顺序。')
                complete = True
            elif not changed and len(records) == total:
                complete = True
            if sweep == 0 and overlap:
                runner.log(f'分页重复 {overlap} 条，已自动合并；当前去重 {len(records)}/{total} 条，继续检索。')
            runner.emit(kind='phase', text=f'ScienceDirect 检索 · 去重已获取 {len(records)}/{total} 条'
                        + (f' · 补查 {sweep}' if sweep else ''))
            persist()
            if complete or reached_end:
                break
            signature = tuple(sorted(set(keys)))
            if not items or signature in page_signatures or pages >= max(3, math.ceil(max(totals) / PAGE_SIZE) + 2):
                runner.log('本轮分页未继续推进，已保留结果并结束本轮，避免重复请求。')
                break
            page_signatures.add(signature)
        if complete or (sweep > 0 and len(records) == before):
            break
    if not records and total:
        raise ActionRequired('ScienceDirect 报告有匹配项，但有限次补查后仍未返回记录；进度已保留，请稍后重试。')
    return {'raw_records': list(records.values()), 'complete': complete, 'results_found': total,
            'unique_received': len(records), 'missing': max(0, total - len(records)),
            'total_changed': changed, 'duplicate_occurrences': duplicates, 'page_requests': requests}
