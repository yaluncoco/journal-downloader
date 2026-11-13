"""Search contract, authorization, caching and download-order regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse

from article_search import (ENDPOINT, collect_search, request_page, search_spec,
                            cache_path, cached_search, expression, validate_search, website_url)
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
        self.assertEqual(query['pub'], [PRESETS[0].name])
        self.assertEqual(query['docId'], [PRESETS[0].issn])

    def test_tls_title_and_fulltext_queries_stay_separate_and_fulltext_has_no_title_filter(self):
        title = search_spec(PRESETS[0], 2020, 2027, 'TLS', 'any', 'title', 'relevance')
        full = search_spec(PRESETS[0], 2020, 2027, 'TLS', 'any', 'fulltext', 'relevance')
        self.assertEqual(title['body']['title'], '"tls"')
        self.assertEqual(full['body']['qs'], '"tls"')
        self.assertNotIn('title', full['body'])
        self.assertNotEqual(cache_path(self.root, title), cache_path(self.root, full))
        self.runner.config.title_filter = 'TLS'
        self.runner.config.filter_mode = 'any'
        self.runner.opener = Opener([response([entry(title='Laser scanning of forests')])])
        self.runner.select_records()
        self.assertEqual(len(self.runner.records), 1)

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

    def test_over_limit_and_empty_incomplete_results_never_cache_success(self):
        cases = [[response([], 6001)], [response([], 1), response([], 1)]]
        for responses in cases:
            with self.subTest(responses=len(responses)), self.assertRaises(ActionRequired):
                self.runner.opener = Opener(responses)
                collect_search(self.runner)
            spec = search_spec(PRESETS[0], 2024, 2024, self.cfg.title_filter, 'native', 'fulltext', 'relevance')
            self.assertIsNone(cached_search(self.runner.state, spec))

    def test_duplicate_page_is_deduplicated_and_missing_hit_recovered(self):
        items = [entry(i) for i in range(1, 104)]
        self.runner.opener = Opener([response(items[:100], 103),
            response([items[99], items[100], items[101]], 103), response([items[102]] + items[:99], 103)])
        result = collect_search(self.runner)
        self.assertTrue(result['complete'])
        self.assertEqual(result['unique_received'], 103)
        self.assertEqual(result['duplicate_occurrences'], 100)
        self.assertEqual([r['doi'] for r in result['records']], [r['doi'] for r in items])
        requests = [json.loads(r.data) for r in self.runner.opener.requests]
        self.assertEqual([(r['display']['offset'], r['display']['sortBy']) for r in requests],
                         [(0, 'relevance'), (100, 'relevance'), (0, 'date')])
        self.assertTrue(all(r['qs'] == self.cfg.title_filter and r['date'] == '2024-2024' for r in requests))
        self.assertFalse(list(self.runner.state.glob('searches/*.partial.json')))

    def test_persistent_overlap_keeps_partial_results_and_next_run_fills_gap(self):
        items = [entry(i) for i in range(1, 104)]
        def pages():
            return [response(items[:100], 103), response([items[99], items[100], items[101]], 103)]
        self.runner.opener = Opener(pages() + pages())
        result = collect_search(self.runner)
        self.assertFalse(result['complete'])
        self.assertEqual((len(result['records']), result['missing']), (102, 1))
        self.assertIn('尚差 1 条', result['notice'])
        self.assertIsNone(cached_search(self.runner.state, result['spec']))
        self.assertEqual(len(self.runner.opener.requests), 4)
        self.runner.opener = Opener([response([items[102]] + items[:99], 103)])
        repaired = collect_search(self.runner)
        self.assertTrue(repaired['complete'])
        self.assertEqual(len(repaired['records']), 103)
        self.assertEqual(collect_search(self.runner), repaired)  # Complete cache needs no request.
        self.assertEqual(len(self.runner.opener.requests), 1)

    def test_partial_query_can_download_and_resume_without_duplicate_fulltexts(self):
        self.cfg.max_downloads = 1
        def stuck():
            return [response([entry(1), entry(1)], 2), response([entry(1), entry(1)], 2)]
        self.runner.opener = Opener(stuck())
        self.runner.fetch = Mock(return_value=(xml(entry(1)['doi']), {'http': 200}))
        self.runner.run()
        summary = load_json(self.runner.state / 'download_summary.json')
        self.assertFalse(summary['search_complete'])
        self.assertEqual((summary['saved'], summary['search_missing'], summary['excluded']), (1, 1, 0))
        again = Downloader(self.cfg)
        again.control.wait = Mock()
        again.opener = Opener([response([entry(2)], 2)])
        again.fetch = Mock(return_value=(xml(entry(2)['doi']), {'http': 200}))
        again.run()
        self.assertEqual((again.reused, again.new_count), (1, 1))
        again.fetch.assert_called_once_with(entry(2)['doi'])

    def test_changing_total_recovers_with_a_complete_stable_sweep(self):
        self.runner.opener = Opener([response([entry(1)], 2), response([entry(2), entry(3)], 3),
                                    response([entry(3), entry(2), entry(1)], 3)])
        result = collect_search(self.runner)
        self.assertTrue(result['complete'])
        self.assertTrue(result['total_changed'])
        self.assertEqual(len(result['records']), 3)
        self.assertEqual([row['doi'] for row in result['records']], [entry(i)['doi'] for i in (1, 2, 3)])

    def test_shrinking_snapshot_discards_old_partial_hits_only_when_new_sweep_complete(self):
        self.runner.opener = Opener([response([entry(1), entry(1)], 2), response([entry(1), entry(1)], 2)])
        self.assertFalse(collect_search(self.runner)['complete'])
        self.runner.opener = Opener([response([entry(2)], 1)])
        result = collect_search(self.runner)
        self.assertTrue(result['complete'])
        self.assertEqual([row['doi'] for row in result['records']], [entry(2)['doi']])

    def test_repeated_whole_page_is_bounded_even_when_offset_is_ignored(self):
        page = [entry(i) for i in range(100)]
        self.runner.opener = Opener([response(page, 6000) for _ in range(4)])
        result = collect_search(self.runner)
        self.assertFalse(result['complete'])
        self.assertEqual(result['missing'], 5900)
        self.assertEqual(len(self.runner.opener.requests), 4)

    def test_duplicate_doi_across_pii_is_counted_without_double_download(self):
        self.runner.opener = Opener([response([entry(1), entry(2, doi=entry(1)['doi'].upper())])])
        result = collect_search(self.runner)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['excluded']['重复 DOI'], 1)

    def test_cancel_preserves_page_progress_and_query_isolation(self):
        items = [entry(i) for i in range(1, 102)]
        self.runner.opener = Opener([response(items[:100], 101)])
        self.runner.control.wait = Mock(side_effect=Cancelled)
        with self.assertRaises(Cancelled):
            collect_search(self.runner)
        self.assertEqual(len(list(self.runner.state.glob('searches/*.partial.json'))), 1)
        self.runner.control.wait = Mock()
        self.cfg.title_filter = 'different query'
        self.runner.opener = Opener([response([entry(999)])])
        self.assertEqual(len(collect_search(self.runner)['records']), 1)

    def test_refresh_does_not_reuse_partial_checkpoint(self):
        self.runner.opener = Opener([response([entry(1), entry(1)], 2), response([entry(1), entry(1)], 2)])
        self.assertFalse(collect_search(self.runner)['complete'])
        self.cfg.refresh = True
        self.runner.opener = Opener([response([entry(2)])])
        result = collect_search(self.runner)
        self.assertEqual([r['doi'] for r in result['records']], [entry(2)['doi']])

    def test_recovery_authentication_failure_still_stops_and_keeps_checkpoint(self):
        self.runner.opener = Opener([response([entry(1), entry(1)], 2),
            urllib.error.HTTPError(ENDPOINT, 403, 'test', {}, None)])
        with self.assertRaises(ActionRequired):
            collect_search(self.runner)
        self.assertEqual(len(list(self.runner.state.glob('searches/*.partial.json'))), 1)

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
