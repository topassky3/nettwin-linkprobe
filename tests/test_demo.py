from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from nettwin.analytics import analyze
from nettwin.config import AnalysisConfig
from nettwin.demo_data import generate_demo_csv
from nettwin.io import load_and_validate_csv


class DemoTests(unittest.TestCase):
    def test_demo_is_reproducible_and_has_expected_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = generate_demo_csv(Path(tmp) / "demo.csv")
            validation = load_and_validate_csv(path)
            self.assertEqual(validation.accepted_rows, 4032)
            self.assertEqual(int(validation.data["over_capacity"].sum()), 0)

            result = analyze(validation.data, AnalysisConfig())
            rows = result.summary.set_index("link_id")
            self.assertEqual(rows.loc["HN_CAMPAMENTO_CRITICO", "status"], "CRÍTICO")
            self.assertIn(rows.loc["HN_YARUMAL_CRECIMIENTO", "status"], {"ALTO", "MEDIO"})
            self.assertTrue(bool(rows.loc["HN_YARUMAL_CRECIMIENTO", "trend_reliable"]))
            self.assertEqual(rows.loc["HN_CEDENO_ESTABLE", "status"], "NORMAL")
            self.assertTrue(rows.loc["HN_CAMPAMENTO_CRITICO", "max_utilization_pct"] < 100)


if __name__ == "__main__":
    unittest.main()
