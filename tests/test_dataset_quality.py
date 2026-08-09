from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from nettwin.dataset_quality import assess_dataset_quality, write_dataset_quality
from scripts.generate_report_debug_run import generate


class DatasetQualityTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        return generate(root)

    def _rewrite_csv(self, path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def _rows(self, path: Path) -> list[dict]:
        with path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_healthy_run_passes_and_reports_expected_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertEqual(result.status, "PASS")
            self.assertTrue(result.safe_for_conclusions)
            self.assertEqual(result.summary["expected_rows_total"], 72)
            self.assertEqual(result.summary["actual_rows_total"], 72)
            self.assertEqual(result.summary["valid_rows_total"], 72)
            self.assertEqual(result.summary["temporal_integrity_pct"], 100.0)
            self.assertEqual(result.summary["gaps_total"], 0)

    def test_missing_sample_creates_gap_and_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "host_samples.csv"
            rows = self._rows(path)
            del rows[10]
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertEqual(result.status, "WARN")
            self.assertTrue(result.safe_for_conclusions)
            self.assertEqual(result.sources["host"]["actual_rows"], 23)
            self.assertGreaterEqual(result.sources["host"]["timestamp_quality"]["gaps"], 1)

    def test_duplicate_timestamp_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "probe_samples.csv"
            rows = self._rows(path)
            rows.insert(4, dict(rows[3]))
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertGreaterEqual(result.sources["probes"]["timestamp_quality"]["duplicates"], 1)
            self.assertEqual(result.status, "WARN")

    def test_out_of_order_timestamp_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "interface_samples.csv"
            rows = self._rows(path)
            rows[5], rows[6] = rows[6], rows[5]
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertGreaterEqual(result.sources["interface"]["timestamp_quality"]["out_of_order"], 1)
            self.assertEqual(result.status, "WARN")

    def test_wrong_interface_is_blocking_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "interface_samples.csv"
            rows = self._rows(path)
            for row in rows:
                row["interface"] = "otra0"
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertEqual(result.status, "FAIL")
            self.assertFalse(result.safe_for_conclusions)
            self.assertFalse(result.summary["observed_interfaces"] == ["eth0"])

    def test_impossible_probe_values_are_blocking_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "probe_samples.csv"
            rows = self._rows(path)
            rows[0]["packets_received"] = "101"
            rows[0]["packets_sent"] = "100"
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertEqual(result.status, "FAIL")
            self.assertFalse(result.safe_for_conclusions)
            self.assertGreater(result.summary["impossible_values_total"], 0)

    def test_invalid_timestamp_is_blocking_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "host_samples.csv"
            rows = self._rows(path)
            rows[0]["timestamp"] = "NO-ES-FECHA"
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertEqual(result.status, "FAIL")
            self.assertFalse(result.safe_for_conclusions)
            self.assertEqual(result.summary["invalid_timestamps_total"], 1)

    def test_unreachable_target_is_warning_not_invented_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            path = run / "probe_samples.csv"
            rows = self._rows(path)
            rows[0]["reachability"] = "False"
            rows[0]["packets_received"] = "0"
            rows[0]["packet_loss_pct"] = "100"
            self._rewrite_csv(path, rows)
            result = assess_dataset_quality(run, integrity_valid=True)
            self.assertEqual(result.status, "WARN")
            self.assertTrue(result.safe_for_conclusions)
            self.assertEqual(result.summary["unreachable_probe_rows"], 1)

    def test_integrity_failure_blocks_conclusions_and_quality_artifacts_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root)
            result = assess_dataset_quality(run, integrity_valid=False)
            self.assertEqual(result.status, "FAIL")
            self.assertFalse(result.safe_for_conclusions)
            output = root / "quality"
            path = write_dataset_quality(result, output)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "dataset-quality-v1")
            self.assertFalse(payload["safe_for_conclusions"])
            self.assertTrue((output / "dataset_quality_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
