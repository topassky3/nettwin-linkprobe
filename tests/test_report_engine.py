from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nettwin.integrity_engine import IntegrityConfig, finalize_integrity
from nettwin.report_cli import build_parser
from nettwin.report_engine import ReportConfig, generate_report
from scripts.generate_report_debug_run import generate


class ReportEngineTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        return generate(root)

    def _sha(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _reseal(self, run: Path) -> None:
        experiment = json.loads((run / "experiment_config.json").read_text(encoding="utf-8"))
        targets = tuple(str(row["name"]) for row in experiment.get("targets", []))
        finalize_integrity(
            run,
            config=IntegrityConfig(
                run_id=str(experiment["run"]["run_id"]),
                interface=str(experiment["interface"]["name"]),
                targets=targets,
                requested_duration_seconds=float(experiment["run"]["duration_seconds"]),
                config_path=run / "experiment_config.json",
                sensor_version="0.3-dev",
                analyzer_version="0.3-dev",
            ),
        )

    def test_report_generates_complete_html_json_and_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            result = generate_report(
                run,
                root / "report",
                config=ReportConfig(company="HacheNet", link_name="LINK-01"),
            )
            self.assertTrue(result.integrity_valid)
            self.assertEqual(result.quality_status, "PASS")
            self.assertTrue(result.safe_for_conclusions)
            self.assertTrue(result.analysis_executed)
            self.assertTrue(result.report_json.exists())
            self.assertTrue(result.report_html.exists())
            self.assertTrue((result.output_dir / "dataset_quality.json").exists())
            for name in (
                "quality_summary.json", "events.json", "correlations.json",
                "evidence_records.json", "event_fingerprints.json",
            ):
                self.assertTrue((result.output_dir / "analysis" / name).exists(), name)
            html = result.report_html.read_text(encoding="utf-8")
            for heading in (
                "Resumen ejecutivo", "Calidad del dataset", "Tabla principal de resultados",
                "Línea temporal maestra", "Eventos y evidencia", "Metodología",
                "Limitaciones", "Anexos técnicos",
            ):
                self.assertIn(heading, html)

    def test_report_contains_event_finding_fingerprint_and_no_causal_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            result = generate_report(run, root / "report")
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(payload["events"]), 1)
            self.assertGreaterEqual(len(payload["findings"]), 1)
            self.assertGreaterEqual(len(payload["fingerprints"]), 1)
            finding = payload["findings"][0]
            self.assertIn("fact", finding)
            self.assertIn("interpretation", finding)
            self.assertIn("hypothesis", finding)
            self.assertIn("confidence", finding)
            self.assertIn("recommendation", finding)
            self.assertFalse(finding["causal_claim_allowed"])
            html = result.report_html.read_text(encoding="utf-8")
            self.assertIn("HECHO.", html)
            self.assertIn("INTERPRETACIÓN.", html)
            self.assertIn("HIPÓTESIS.", html)
            self.assertIn("RECOMENDACIÓN.", html)
            self.assertIn("Señales simultáneas por bucket", html)
            self.assertIn("Siguiente etapa comercial", html)

    def test_missing_capacity_stays_nd_and_nic_speed_is_not_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            path = run / "experiment_config.json"
            experiment = json.loads(path.read_text(encoding="utf-8"))
            experiment["interface"]["capacity_mbps"] = None
            path.write_text(json.dumps(experiment, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            self._reseal(run)
            result = generate_report(run, root / "report")
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))
            self.assertIsNone(payload["capacity_mbps"])
            self.assertIsNone(payload["headline_metrics"]["utilization_mean_pct"])
            self.assertIsNone(payload["headline_metrics"]["utilization_p95_pct"])
            self.assertTrue(any("Utilización = N/D" in row for row in payload["methodology"]))
            self.assertIn("N/D", result.report_html.read_text(encoding="utf-8"))

    def test_report_is_byte_deterministic_across_output_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            first = generate_report(run, root / "report-a")
            second = generate_report(run, root / "report-b")
            self.assertEqual(first.report_json.read_bytes(), second.report_json.read_bytes())
            self.assertEqual(first.report_html.read_bytes(), second.report_html.read_bytes())

    def test_report_does_not_modify_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            manifest = run / "checksums.sha256"
            before = self._sha(manifest)
            generate_report(run, root / "report")
            after = self._sha(manifest)
            self.assertEqual(before, after)

    def test_quality_fail_blocks_analysis_but_still_writes_diagnostic_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            path = run / "experiment_config.json"
            experiment = json.loads(path.read_text(encoding="utf-8"))
            experiment["interface"]["name"] = "wrong0"
            path.write_text(json.dumps(experiment, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            self._reseal(run)
            result = generate_report(run, root / "report")
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))
            self.assertEqual(result.quality_status, "FAIL")
            self.assertFalse(result.safe_for_conclusions)
            self.assertFalse(result.analysis_executed)
            self.assertFalse(payload["analysis_executed"])
            self.assertEqual(payload["events"], [])
            self.assertEqual(payload["findings"], [])
            self.assertTrue(result.report_html.exists())
            self.assertIn("Conclusiones habilitadas: <b>NO</b>", result.report_html.read_text(encoding="utf-8"))

    def test_report_output_inside_sealed_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            with self.assertRaisesRegex(ValueError, "fuera del directorio sellado"):
                generate_report(run, run / "reports")

    def test_cli_exposes_phase13_options(self):
        args = build_parser().parse_args([
            "--run", "runs/RUN-001", "--output", "reports/RUN-001",
            "--company", "HacheNet", "--link", "LINK-01",
            "--capacity-mbps", "100", "--pdf",
        ])
        self.assertEqual(args.run, "runs/RUN-001")
        self.assertEqual(args.output, "reports/RUN-001")
        self.assertEqual(args.company, "HacheNet")
        self.assertEqual(args.link, "LINK-01")
        self.assertEqual(args.capacity_mbps, 100.0)
        self.assertTrue(args.pdf)

    def test_report_source_hash_matches_exact_experiment_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run(root / "source")
            result = generate_report(run, root / "report")
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))
            expected = self._sha(run / "experiment_config.json")
            self.assertEqual(payload["source_sha256"]["experiment_config.json"], expected)
            self.assertEqual(len(payload["report_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
