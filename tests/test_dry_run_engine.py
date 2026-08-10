from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nettwin.dry_run_engine import (
    DryRunSettings,
    build_dry_run_config,
    run_dry_run,
    select_local_interface,
)
from nettwin.integrity_engine import IntegrityConfig, finalize_integrity, verify_integrity
from nettwin.orchestrator_engine import OrchestratorResult
from nettwin.report_engine import generate_report
from scripts.generate_report_debug_run import generate


class DryRunEngineTests(unittest.TestCase):
    def _synthetic_orchestrator(self, config_path: str | Path, **_kwargs) -> OrchestratorResult:
        root = Path(config_path).resolve().parent
        run = generate(root)
        log_path = run / "logs" / "linkprobe.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("dry-run synthetic capture log\n", encoding="utf-8")
        experiment = json.loads((run / "experiment_config.json").read_text(encoding="utf-8"))
        finalize_integrity(
            run,
            config=IntegrityConfig(
                run_id=str(experiment["run"]["run_id"]),
                interface=str(experiment["interface"]["name"]),
                targets=tuple(str(row["name"]) for row in experiment["targets"]),
                requested_duration_seconds=float(experiment["run"]["duration_seconds"]),
                config_path=run / "experiment_config.json",
                sensor_version="0.3-dev",
                analyzer_version="0.3-dev",
            ),
        )
        verified = verify_integrity(run, strict_untracked=True)
        self.assertTrue(verified.valid)
        return OrchestratorResult(
            run_dir=run,
            status="completed",
            stop_reason="duration_elapsed",
            preflight_ready=True,
            orchestration_path=run / "orchestration.json",
            integrity_valid=True,
            checksums_path=run / "checksums.sha256",
            metadata_path=run / "run_metadata.json",
            integrity_report_path=verified.report_path,
        )

    def test_settings_reject_invalid_window_and_run_id(self):
        with self.assertRaisesRegex(ValueError, "duration_seconds"):
            DryRunSettings(duration_seconds=0).validate()
        with self.assertRaisesRegex(ValueError, "dos ciclos"):
            DryRunSettings(duration_seconds=3, interval_seconds=2).validate()
        with self.assertRaisesRegex(ValueError, "run_id"):
            DryRunSettings(run_id="bad run id").validate()

    def test_config_is_loopback_only_and_documents_expected_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = build_dry_run_config(
                root,
                DryRunSettings(duration_seconds=12, interval_seconds=2),
                interface_name="Wi-Fi",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["targets"], [{
                "name": "local_loopback",
                "address": "127.0.0.1",
                "role": "phase14_local_loopback_only",
            }])
            self.assertEqual(payload["dry_run"]["expected_cycles_per_collector"], 6)
            self.assertEqual(payload["dry_run"]["active_probe_scope"], "loopback_only")
            self.assertFalse(payload["dry_run"]["external_network_probe_performed"])
            self.assertEqual(payload["output"]["directory"], "./run")

    def test_manual_interface_is_preserved_for_preflight_validation(self):
        name, reason = select_local_interface("INTERFAZ-ELEGIDA")
        self.assertEqual(name, "INTERFAZ-ELEGIDA")
        self.assertIn("manual", reason)

    def test_nonempty_workspace_is_rejected_before_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dry"
            root.mkdir()
            (root / "evidencia.txt").write_text("no sobrescribir", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ya contiene archivos"):
                run_dry_run(
                    root,
                    settings=DryRunSettings(interface_name="eth0"),
                    interface_selector=lambda preferred: (preferred or "eth0", "test"),
                    orchestrator_fn=self._synthetic_orchestrator,
                )

    def test_full_pipeline_passes_and_preserves_sealed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dry"
            result = run_dry_run(
                root,
                settings=DryRunSettings(interface_name="eth0"),
                interface_selector=lambda preferred: (preferred or "eth0", "test fixture"),
                orchestrator_fn=self._synthetic_orchestrator,
            )
            self.assertEqual(result.status, "PASS")
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(all(value == "PASS" for value in summary["stages"].values()))
            self.assertTrue(summary["integrity_preservation"]["sealed_run_unchanged"])
            self.assertFalse(summary["security"]["external_network_probe_performed"])
            self.assertTrue((result.report_dir / "analysis.json").exists())
            self.assertTrue((result.report_dir / "report.html").exists())
            self.assertTrue((result.report_dir / "analysis" / "events.json").exists())

    def test_capture_failure_blocks_report_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dry"

            def blocked(config_path, **_kwargs):
                run = Path(config_path).parent / "run"
                run.mkdir(parents=True, exist_ok=True)
                return OrchestratorResult(
                    run_dir=run,
                    status="preflight_blocked",
                    stop_reason="preflight_not_ready",
                    preflight_ready=False,
                    orchestration_path=run / "orchestration.json",
                    integrity_valid=None,
                    checksums_path=None,
                    metadata_path=None,
                    integrity_report_path=None,
                )

            result = run_dry_run(
                root,
                settings=DryRunSettings(interface_name="eth0"),
                interface_selector=lambda preferred: (preferred or "eth0", "test"),
                orchestrator_fn=blocked,
                report_fn=lambda *_args, **_kwargs: self.fail("report no debe ejecutarse"),
            )
            self.assertEqual(result.status, "FAIL")
            self.assertEqual(result.failed_stage, "preflight_capture")
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["stages"]["analysis_report"], "PENDING")

    def test_post_report_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dry"

            def report_and_tamper(run_dir, output_dir, *, config):
                result = generate_report(run_dir, output_dir, config=config)
                path = Path(run_dir) / "interface_samples.csv"
                path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                return result

            result = run_dry_run(
                root,
                settings=DryRunSettings(interface_name="eth0"),
                interface_selector=lambda preferred: (preferred or "eth0", "test"),
                orchestrator_fn=self._synthetic_orchestrator,
                report_fn=report_and_tamper,
            )
            self.assertEqual(result.status, "FAIL")
            self.assertEqual(result.failed_stage, "integrity_after_report")
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["integrity_preservation"]["sealed_run_unchanged"])


if __name__ == "__main__":
    unittest.main()
