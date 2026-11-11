"""Search contract, authorization, caching and download-order regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

from article_search import (ENDPOINT, collect_search, request_page, search_spec,
                            cache_path, expression, validate_search, website_url)
from engine import Config, Downloader, PRESETS, ActionRequired, Cancelled, load_json
from smoke_test import cached_index, xml
from test_engine import Opener, Response


def entry(index=1, **changes):
    return {"doi": f"10.1016/j.example.2024.{index}", "title": f"Article {index}",
            "pii": f"S000000000000{index:04}", "sourceTitle": PRESETS[0].name,
            "publicationDate": "2024-03-01", **changes}


def response(items, total=None):
    return Response(json.dumps({"resultsFound": len(items) if total is None else total, "results": items}).encode())


class ArticleSearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        state = patch('library_store.STATE_HOME', self.root / 'state')
        state.start()
        self.addCleanup(state.stop)
        self.cfg = Config('SYNTHETIC_SEARCH_KEY', PRESETS[0].url, 2024, 2024, self.root / 'papers',
            title_filter='forest AND "deep learning"', filter_mode='native', search_scope='fulltext', interval=0)
        self.runner = Downloader(self.cfg)
        self.runner.base = self.cfg.output / PRESETS[0].issn
        self.runner.journal = PRESETS[0]
        self.runner.control.wait = Mock(side_effect=lambda *args: self.runner.control.checkpoint())

    def test_native_and_phrase_inputs_do_not_use_local_substring_matching(self):
        value = '(forest OR crop) AND "deep learning" NOT urban'
        self.assertEqual(expression(value, 'native'), value)
        self.assertEqual(expression('forest，deep learning', 'any'), '"forest" OR "deep learning"')
        self.assertEqual(expression('forest;deep learning', 'all'), '"forest" AND "deep learning"')
        self.assertEqual(expression('“deep learning”', 'native'), '"deep learning"')
        for query in ('(', ')a(', '"forest', 'x' * 251):
            with self.subTest(query=query), self.assertRaises(ValueError):
                expression(query, 'native')
        with self.assertRaises(ValueError):
            validate_search('forest AND crop', 'native', 'local', 'relevance')

    def test_official_put_contract_and_no_credentials_in_body_or_cache(self):
        self.runner.opener = Opener([response([entry(title='Title without the query words')])])
        result = collect_search(self.runner)
        self.assertEqual(len(result['records']), 1)  # Match may be in the full text.
        request = self.runner.opener.requests[0]
        self.assertEqual((request.full_url, request.method), (ENDPOINT, 'PUT'))
        self.assertEqual(request.get_header('X-els-apikey'), self.cfg.api_key)
        body = json.loads(request.data)
        self.assertEqual(body, {'pub': '"' + PRESETS[0].name + '"', 'date': '2024-2024',
            'qs': self.cfg.title_filter, 'display': {'sortBy': 'relevance', 'offset': 0, 'show': 100}})
        for path in self.runner.state.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.cfg.api_key, path.read_text(encoding='utf-8'))

    def test_title_and_website_use_same_expression_years_and_sort(self):
        spec = search_spec(PRESETS[0], 2024, 2026, 'forest, crop', 'all', 'title', 'date')
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(website_url(PRESETS[0], 2024, 2026,
            'forest, crop', 'all', 'title', 'date')).query)
        self.assertNotIn('qs', spec['body'])
        self.assertEqual(query['title'], [spec['body']['title']])
        self.assertEqual(query['date'], ['2024-2026'])
        self.assertEqual(query['sortBy'], ['date'])

    def test_pagination_preserves_relevance_order_and_throttles(self):
        items = [entry(i) for i in range(103, 0, -1)]
        self.runner.opener = Opener([response(items[:100], 103), response(items[100:], 103)])
        result = collect_search(self.runner)
        self.assertEqual([r['doi'] for r in result['records']], [r['doi'] for r in items])
        self.assertEqual([json.loads(r.data)['display']['offset'] for r in self.runner.opener.requests], [0, 100])
        self.assertGreaterEqual(self.runner.control.wait.call_args.args[0], 1)

    def test_cache_separates_scope_query_sort_and_years(self):
        self.runner.opener = Opener([response([entry()])])
        first = collect_search(self.runner)
        self.assertEqual(collect_search(self.runner), first)
        self.assertEqual(len(self.runner.opener.requests), 1)
        original = cache_path(self.runner.state, first['spec'])
        for scope, sort, query, end in [('title', 'relevance', self.cfg.title_filter, 2024),
                                       ('fulltext', 'date', self.cfg.title_filter, 2024),
                                       ('fulltext', 'relevance', 'crop', 2024),
                                       ('fulltext', 'relevance', self.cfg.title_filter, 2025)]:
            other = search_spec(PRESETS[0], 2024, end, query, 'native', scope, sort)
            self.assertNotEqual(cache_path(self.runner.state, other), original)
        self.cfg.refresh = True
        self.runner.opener = Opener([response([])])
        self.assertEqual(collect_search(self.runner)['records'], [])

    def test_wrong_journal_year_and_missing_doi_are_reported(self):
        self.runner.opener = Opener([response([entry(1), entry(2, sourceTitle='Remote Sensing Letters'),
            entry(3, publicationDate='2023-12-31'), entry(4, doi=None), entry(5, publicationDate='bad')])])
        result = collect_search(self.runner)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(sum(result['excluded'].values()), 4)
        self.assertEqual(result['results_found'], 5)

    def test_over_limit_incomplete_and_changing_results_never_cache_success(self):
        cases = [[response([], 6001)], [response([], 1)],
                 [response([entry()], 2), response([entry(2)], 3)],
                 [response([entry()], 2), response([entry()], 2)]]
        for responses in cases:
            with self.subTest(responses=len(responses)), self.assertRaises(ActionRequired):
                self.runner.opener = Opener(responses)
                collect_search(self.runner)
            self.assertFalse(list(self.runner.state.glob('searches/*.json')))

    def test_permissions_syntax_and_exhausted_quota_do_not_fallback(self):
        for code in (400, 401, 403, 429):
            with self.subTest(code=code), self.assertRaises(ActionRequired):
                self.runner.opener = Opener([urllib.error.HTTPError(ENDPOINT, code, 'test',
                    {'X-RateLimit-Remaining': '0'}, None)])
                collect_search(self.runner)
            self.assertEqual(len(self.runner.opener.requests), 1)
        self.assertFalse(list(self.runner.state.glob('searches/*.json')))

    def test_transient_retry_and_cancellation(self):
        self.runner.opener = Opener([urllib.error.HTTPError(ENDPOINT, 503, 'test', {'Retry-After': '5'}, None), response([])])
        self.assertEqual(request_page(self.runner, {})['resultsFound'], 0)
        self.runner.control.wait.assert_called_once_with(5, '搜索请求暂时失败，等待重试')
        self.runner.control.stop.set()
        with self.assertRaises(Cancelled):
            request_page(self.runner, {})
        self.assertEqual(len(self.runner.opener.requests), 2)

    def test_malformed_success_and_echoed_key_are_not_saved(self):
        for payload in ({'results': []}, {'resultsFound': True, 'results': []},
                        {'resultsFound': 0, 'results': [], 'message': self.cfg.api_key}):
            self.runner.opener = Opener([Response(json.dumps(payload).encode())])
            with self.subTest(payload=list(payload)), self.assertRaises(ActionRequired):
                collect_search(self.runner)
        self.assertFalse(list(self.runner.state.glob('searches/*.json')))

    def test_preview_never_fetches_and_does_not_overwrite_unfiltered_manifest(self):
        self.runner.base.mkdir(parents=True)
        cached_index(self.runner.base, [{'doi': '10.1016/j.old', 'year': 2024, 'date': '2024', 'title': 'Old'}])
        self.runner.state.mkdir(parents=True, exist_ok=True)
        manifest = self.runner.state / 'manifest.jsonl'
        manifest.write_text('preserve original full list', encoding='utf-8')
        self.runner.opener = Opener([response([entry()])])
        with patch.object(self.runner, 'fetch', side_effect=AssertionError('preview downloaded')):
            result = self.runner.preview()
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(manifest.read_text(encoding='utf-8'), 'preserve original full list')

    def test_cached_search_download_limit_and_resume_keep_api_order(self):
        self.runner.opener = Opener([response([entry(3), entry(1), entry(2)])])
        self.cfg.max_downloads = 1
        fetched = []
        def fetch(doi):
            fetched.append(doi)
            return xml(doi), {'http': 200}
        self.runner.fetch = fetch
        self.runner.run()
        again = Downloader(self.cfg)
        again.opener = Opener([])  # Cached query and checked local XML must suffice.
        again.fetch = fetch
        again.run()
        self.assertEqual(fetched, [entry(3)['doi'], entry(1)['doi']])
        self.assertEqual(again.reused, 1)

    def test_empty_query_reuses_full_index_without_search_requests(self):
        self.cfg.title_filter = ''
        cached_index(self.runner.base, [{'doi': '10.1016/j.old', 'year': 2024, 'date': '2024', 'title': 'Old'}])
        self.runner.opener = Opener([])
        result = self.runner.preview()
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(self.runner.opener.requests, [])


if __name__ == '__main__':
    unittest.main()
