import copy
import json
import unittest
from pathlib import Path

from tools.verify_evidence import verify


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = json.loads((Path(__file__).resolve().parents[1] /
                                 'research/extension-evidence.json').read_text(encoding='utf-8'))

    def test_frozen_batch_recomputes_and_retains_regression(self):
        report = verify(self.bundle)
        self.assertEqual(report['summary']['n'], 24)
        negative = next(row for row in report['rows'] if row['index'] == 969)
        self.assertLess(negative['adaptive_quality_10'], negative['direct_quality_10'])

    def test_missing_case_is_rejected(self):
        changed = copy.deepcopy(self.bundle)
        changed['details'].pop()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            verify(changed)

    def test_inconsistent_score_is_rejected(self):
        changed = copy.deepcopy(self.bundle)
        changed['direct_scores'][0]['direct']['mean_10'] = -1
        with self.assertRaisesRegex(ValueError, 'mean'):
            verify(changed)
