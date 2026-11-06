import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from engine import Config, Downloader, PRESETS, local_journal, parse_journal_url, valid_issn
from journal_catalog import catalog, search
from journal_metadata import details, category_matches, reference_label


class CatalogTests(unittest.TestCase):
    def test_user_prefix_plural_ampersand_and_specific_completion(self):
        results = search('computer and')
        self.assertTrue(results)
        self.assertIn('0168-1699', [item['issn'] for item in results])
        self.assertEqual([i['issn'] for i in results], [i['issn'] for i in search('computers &')])
        self.assertTrue(all(item['name'].startswith('Computer') for item in results[:5]))
        for query in ('computer and electronic', 'Computers & Electronics', 'Computers and Electronics in Agriculture'):
            self.assertEqual(search(query)[0]['issn'], '0168-1699')
        for query in ('!!!', '农学', '不存在的期刊'):
            self.assertEqual(search(query), [])

    def test_exact_primary_title_stays_first_with_aliases(self):
        for item in catalog()['journals']:
            if item.get('aliases'):
                self.assertEqual(search(item['name'])[0]['issn'], item['issn'])
                break

    def test_official_intersections_and_reference_provenance(self):
        agriculture = search('0168-1699')[0]
        self.assertTrue(category_matches(agriculture, '农学 × 计算机'))
        self.assertTrue(category_matches(agriculture, '林学 × 计算机'))
        self.assertEqual(details(agriculture)['cas2025']['zone'], 1)
        self.assertEqual(details(agriculture)['cas2025']['year'], 2025)
        self.assertIn('参考', reference_label(agriculture))
        self.assertEqual(search('computer and', category='农学 × 计算机', zone='1区', top=True)[0]['issn'], '0168-1699')
        self.assertFalse(category_matches(search('0034-4257')[0], '林学 × 计算机'))
        self.assertTrue(all(category_matches(i, '计算机') for i in search('', category='计算机')))
        self.assertTrue(all('cas2025' not in details(i) for i in search('', zone='未收录')))

    def test_official_snapshot_has_valid_unique_identifiers_and_safe_urls(self):
        data = catalog()
        self.assertGreater(data["count"], 5000)
        self.assertEqual(len({item["issn"] for item in data["journals"]}), data["count"])
        for item in data["journals"]:
            self.assertTrue(valid_issn(item["issn"]))
            parse_journal_url(item["url"])
        self.assertEqual(len(data["sources"]), 3)

    def test_search_handles_name_words_issn_shortcut_and_status(self):
        for query in ("Remote Sensing of Environment", "environment remote sensing", "0034-4257", "00344257", "rse"):
            self.assertIn("0034-4257", [item["issn"] for item in search(query)])
        self.assertEqual(search("zzzz no such journal zzzz"), [])
        self.assertTrue(all(item["status"] == "Active" for item in search("", "在刊")))
        history = search("", "历史刊名")
        self.assertTrue(history)
        self.assertTrue(all(item["status"] not in ("Active", "Forthcoming") for item in history))

    def test_catalogue_resolves_offline_and_presets_are_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config("SYNTHETIC_KEY", "https://www.sciencedirect.com/journal/agricultural-and-forest-meteorology",
                         2024, 2024, Path(tmp))
            runner = Downloader(cfg)
            runner.public_json = lambda *_: self.fail("Known catalogue title must resolve offline")
            self.assertEqual(runner.resolve().issn, "0168-1923")
            cfg.url = "https://www.sciencedirect.com/science/journal/01681923"
            self.assertEqual(runner.resolve().issn, "0168-1923")
        for preset in PRESETS:
            self.assertEqual(local_journal(preset.url), preset)
            self.assertEqual(local_journal("https://www.sciencedirect.com/science/journal/" + preset.issn.replace("-", "")), preset)

    def test_non_sciencedirect_catalog_urls_are_rejected(self):
        for value in ("https://evil.invalid/science/journal/00344257", "https://www.sciencedirect.com@evil.invalid/science/journal/00344257"):
            with self.assertRaises(ValueError):
                local_journal(value)
