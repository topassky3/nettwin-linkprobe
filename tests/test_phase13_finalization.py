from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

from nettwin.report_engine import generate_report
from scripts.generate_report_debug_run import generate


class Phase13FinalizationTests(unittest.TestCase):
    def test_limitations_use_observed_duration_instead_of_hardcoded_four_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = generate(root / "source")
            result = generate_report(run, root / "report")

            payload = json.loads(result.report_json.read_text(encoding="utf-8"))
            limitations = payload["limitations"]
            self.assertTrue(
                any("2.00 minutos" in row for row in limitations),
                msg=f"Limitaciones sin duración real: {limitations}",
            )
            self.assertFalse(
                any("cuatro horas" in row.lower() for row in limitations),
                msg=f"Persistió texto hardcodeado: {limitations}",
            )

            html = result.report_html.read_text(encoding="utf-8")
            self.assertIn("2.00 minutos", html)
            self.assertNotIn("cuatro horas", html.lower())

    def test_nonnegative_metrics_never_render_negative_axis_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = generate(root / "source")
            result = generate_report(run, root / "report")
            html = result.report_html.read_text(encoding="utf-8")

            axis_labels = re.findall(
                r'class="axis-text">\s*([^<]+)</text>',
                html,
            )
            negative = [label for label in axis_labels if label.strip().startswith("-")]
            self.assertEqual(
                negative,
                [],
                msg=f"Se renderizaron ticks negativos en métricas no negativas: {negative}",
            )
            self.assertIn("0.00%", html)
            self.assertIn("0.00", html)


if __name__ == "__main__":
    unittest.main()
