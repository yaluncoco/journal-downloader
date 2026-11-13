"""Unfiltered journal contents via Elsevier Article Metadata, without a browser.

Contract: dev.elsevier.com/documentation/ArticleMetadataAPI.wadl
Fields: dev.elsevier.com/sd_article_meta_tips.html (ISSN has no hyphens).
"""
import re
import urllib.parse
import urllib.request

ENDPOINT = "https://api.elsevier.com/content/metadata/article"


def catalog_request(body, headers):
    start, end = map(int, body['date'].split('-'))
    query = (f"issn({body['issn'].replace('-', '')}) AND pub-date AFT {start-1}1231"
             f" AND pub-date BEF {end+1}0101")
    params = {'query': query, 'count': body['display']['show'], 'start': body['display']['offset']}
    return urllib.request.Request(ENDPOINT + '?' + urllib.parse.urlencode(params), headers=headers)


def catalog_response(data, body):
    from engine import ActionRequired
    try:
        result = data['search-results']
        total = result['opensearch:totalResults']
        offset = result['opensearch:startIndex']
        items = result['entry']
        if (not str(total).isdigit() or not str(offset).isdigit() or not isinstance(items, list)
                or int(offset) != body['display']['offset']):
            raise ValueError()
        if int(total) == 0 and (not items or all(isinstance(row, dict)
                and row.get('error') == 'Result set was empty' for row in items)):
            return {'resultsFound': 0, 'results': []}
        rows = []
        for item in items:
            if not isinstance(item, dict) or 'error' in item:
                raise ValueError()
            rows.append({'doi': item.get('prism:doi'), 'title': item.get('dc:title'),
                'publicationDate': item.get('prism:coverDate'), 'sourceTitle': item.get('prism:publicationName'),
                'issn': re.sub(r'[^\dX]', '', str(item.get('prism:issn') or '').upper()),
                'pii': re.sub(r'[^A-Za-z0-9]', '', str(item.get('pii') or '')),
                'uri': item.get('prism:url')})
        return {'resultsFound': int(total), 'results': rows}
    except (KeyError, TypeError, ValueError):
        raise ActionRequired('Elsevier 期刊目录响应不完整，未将其当作零篇；请稍后重试，已有文件已保留。') from None
