"""Billing/invoice ingestion registry: descriptions only, never authority."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ingest import billing_profiles as bp  # noqa: E402


class BillingProfileTests(unittest.TestCase):
    def test_registry_valid(self):
        self.assertGreaterEqual(bp.validate_registry(), 60)

    def test_unknown_id_fails(self):
        with self.assertRaises(KeyError):
            bp.describe('NOT_A_PROFILE')

    def test_describe_isolated(self):
        d = bp.describe('CMS1500')
        d['fields']['billed'] = 'tampered'
        self.assertNotEqual(bp.PROFILES['CMS1500']['fields']['billed'], 'tampered')
        self.assertIn('provenance', d)
        self.assertEqual(len(d['rules']), len(bp.RULES))

    def test_candidates_returns_all_matches_no_winner(self):
        text = 'UB-04 ... see attached Explanation of Benefits'
        self.assertEqual(sorted(bp.candidates(text)), ['CMS1450', 'EOB'])

    def test_marker_word_boundary(self):
        self.assertEqual(bp.candidates('UB-045 reference'), [])

    def test_native_edi_has_no_markers(self):
        for k, p in bp.PROFILES.items():
            if p['route'] in ('versioned_adapter', 'fhir_adapter', 'xml_adapter', 'pdf_xml_adapter'):
                self.assertEqual(p['markers'], [], k)

    def test_no_network_imports(self):
        src = (pathlib.Path(bp.__file__)).read_text(encoding='utf-8')
        for mod in ('urllib', 'requests', 'http.client', 'socket'):
            self.assertNotIn('import ' + mod, src)


if __name__ == '__main__':
    unittest.main()
