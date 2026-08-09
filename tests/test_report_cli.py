from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nettwin.report_cli import write_analysis_manifest
from nettwin.report_engine import generate_report
from scripts.generate_report_debug_run import generate


class ReportCliTests(unittest.TestCase):
    def test_analysis_manifest_summarizes_canonical_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = generate(root / "source")
            result = generate_report(run, root / "report")
            path = write_analysis_manifest(result)
            payload = json.loads(path.read_text(encoding="utf-8"))
            report = json.loads(result.report_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "linkprobe-analysis-v1")
            self.assertEqual(payload["run_id"], report["run_id"])
            self.assertTrue(payload["analysis_executed"])
            self.assertTrue(payload["safe_for_conclusions"])
            self.assertEqual(payload["event_count"], len(report["events"]))
            self.assertEqual(payload["finding_count"], len(report["findings"]))
            self.assertEqual(payload["fingerprint_count"], len(report["fingerprints"]))
            self.assertEqual(payload["report_sha256"], report["report_sha256"])
            self.assertFalse(payload["causal_inference_performed"])


if __name__ == "__main__":
    unittest.main()
