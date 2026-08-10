from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nettwin import __version__
from nettwin.integrity_engine import IntegrityConfig, finalize_integrity, verify_integrity
from nettwin.orchestrator_engine import OrchestratorResult
from nettwin.pilot_engine import (
    LOCAL_MODE,
    PILOT_SCHEMA_VERSION,
    PRODUCTION_DURATION_SECONDS,
    PRODUCTION_MODE,
    build_pilot_config,
    run_pilot,
    validate_pilot_config,
)
from nettwin.report_engine import generate_report
from scripts.generate_report_debug_run import generate as generate_report_debug_run


class PilotEngineTests(unittest.TestCase):
    def _production_payload(self, root: Path, *, targets: list[dict] | None = None) -> dict:
        return {
            "run": {"run_id": "HACHENET-20260810-LINK01-001", "duration_seconds": 14400},
            "interface": {"name": "eth0", "interval_seconds": 5, "capacity_mbps": 100.0},
            "host": {"interval_seconds": 5},
            "probes": {"interval_seconds": 5, "timeout_ms": 1000, "payload_bytes": 32, "count_per_target": 1},
            "targets": targets or [
                {"name": "internal", "address": "10.0.0.1", "role": "internal_authorized", "authorized": True},
                {"name": "controlled_external", "address": "10.0.0.2", "role": "controlled_external", "authorized": True},
                {"name": "external_reference", "address": "10.0.0.3", "role": "external_reference", "authorized": True},
            ],
            "output": {"directory": "./run", "minimum_free_disk_mb": 100},
            "pilot": {
                "schema_version": PILOT_SCHEMA_VERSION,
                "phase": 15,
                "mode": PRODUCTION_MODE,
                "client": "HacheNet",
                "link_name": "LINK-01",
                "report_directory": "./report",
                "summary_path": "./pilot_summary.json",
                "authorization": {
                    "confirmed": True,
                    "reference": "HACHENET-APPROVAL-TEST",
                    "approved_by": "admin-hachenet",
                },
                "safety": {
                    "payload_capture": False,
                    "unauthorized_discovery": False,
                    "aggressive_throughput_test": False,
                },
            },
        }

    def _write(self, root: Path, payload: dict, name: str = "pilot.json") -> Path:
        path = root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _synthetic_orchestrator(self, config_path: str | Path, **_kwargs) -> OrchestratorResult:
        """Fixture equivalente al contrato real de Fase 12.

        El generador de Fase 13 crea un run analítico sellado, pero no incluye el
        log operacional porque ese archivo pertenece al Orquestador. Para que la
        prueba de Fase 15 simule realmente un OrchestratorResult válido, añadimos
        logs/linkprobe.log y volvemos a sellar/verificar el run, igual que hace la
        aceptación sintética ya validada en Fase 14.
        """
        root = Path(config_path).resolve().parent
        run = generate_report_debug_run(root)
        log_path = run / "logs" / "linkprobe.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("phase15 synthetic capture log\n", encoding="utf-8")

        experiment = json.loads((run / "experiment_config.json").read_text(encoding="utf-8"))
        finalize_integrity(
            run,
            config=IntegrityConfig(
                run_id=str(experiment["run"]["run_id"]),
                interface=str(experiment["interface"]["name"]),
                targets=tuple(str(row["name"]) for row in experiment["targets"]),
                requested_duration_seconds=float(experiment["run"]["duration_seconds"]),
                config_path=run / "experiment_config.json",
                sensor_version="0.3.0",
                analyzer_version="0.3.0",
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

    def test_phase15_uses_v030_release_metadata(self):
        self.assertEqual(__version__, "0.3.0")

    def test_production_template_is_four_hours_but_deliberately_not_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build_pilot_config(Path(tmp) / "pilot.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run"]["duration_seconds"], PRODUCTION_DURATION_SECONDS)
            self.assertEqual(payload["probes"]["interval_seconds"], 5)
            self.assertFalse(payload["pilot"]["authorization"]["confirmed"])
            self.assertFalse(payload["targets"][0]["authorized"])
            result = validate_pilot_config(path)
            self.assertFalse(result.valid)
            self.assertEqual(result.mode, PRODUCTION_MODE)
            joined = "\n".join(result.errors)
            self.assertIn("REEMPLAZAR_IP_AUTORIZADA", joined)

    def test_local_acceptance_config_is_loopback_only_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build_pilot_config(
                Path(tmp) / "pilot_local.json",
                local_acceptance=True,
                interface_name="eth0",
            )
            result = validate_pilot_config(path)
            self.assertTrue(result.valid, result.errors)
            self.assertEqual(result.mode, LOCAL_MODE)
            self.assertEqual(len(result.experiment.targets), 1)
            self.assertEqual(result.experiment.targets[0].address, "127.0.0.1")
            self.assertEqual(result.estimate["icmp_echo_requests"], 6)
            self.assertEqual(result.estimate["expected_rows"]["total"], 18)

    def test_valid_three_target_production_config_has_documented_four_hour_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(root, self._production_payload(root))
            result = validate_pilot_config(path)
            self.assertTrue(result.valid, result.errors)
            self.assertEqual(result.estimate["duration_hours"], 4.0)
            self.assertEqual(result.estimate["probe_cycles"], 2880)
            self.assertEqual(result.estimate["icmp_echo_requests"], 8640)
            self.assertEqual(result.estimate["outbound_icmp_payload_bytes_estimate"], 276480)
            self.assertEqual(result.estimate["expected_rows"]["total"], 14400)

    def test_production_requires_authorization_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self._production_payload(root)
            payload["pilot"]["authorization"] = {"confirmed": False, "reference": "", "approved_by": ""}
            result = validate_pilot_config(self._write(root, payload))
            self.assertFalse(result.valid)
            joined = "\n".join(result.errors)
            self.assertIn("confirmed", joined)
            self.assertIn("reference", joined)
            self.assertIn("approved_by", joined)

    def test_production_rejects_loopback_and_documentation_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = [
                {"name": "bad_loopback", "address": "127.0.0.1", "role": "internal", "authorized": True},
                {"name": "bad_example", "address": "203.0.113.10", "role": "external", "authorized": True},
            ]
            result = validate_pilot_config(self._write(root, self._production_payload(root, targets=targets)))
            self.assertFalse(result.valid)
            joined = "\n".join(result.errors)
            self.assertIn("loopback", joined)
            self.assertIn("documentation/example", joined)

    def test_production_rejects_more_than_three_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = [
                {"name": f"t{index}", "address": f"10.0.0.{index}", "role": "authorized", "authorized": True}
                for index in range(1, 5)
            ]
            result = validate_pilot_config(self._write(root, self._production_payload(root, targets=targets)))
            self.assertFalse(result.valid)
            self.assertTrue(any("máximo 3 targets" in item for item in result.errors))

    def test_production_rejects_wrong_duration_and_aggressive_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self._production_payload(root)
            payload["run"]["duration_seconds"] = 3600
            payload["probes"]["interval_seconds"] = 0.5
            payload["probes"]["payload_bytes"] = 512
            payload["probes"]["count_per_target"] = 10
            result = validate_pilot_config(self._write(root, payload))
            self.assertFalse(result.valid)
            joined = "\n".join(result.errors)
            self.assertIn("14400", joined)
            self.assertIn("interval_seconds", joined)
            self.assertIn("payload_bytes", joined)
            self.assertIn("count_per_target", joined)

    def test_production_run_requires_second_authorization_gate_without_calling_orchestrator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(root, self._production_payload(root))
            called = {"value": False}

            def forbidden_orchestrator(*args, **kwargs):
                called["value"] = True
                raise AssertionError("No debe ejecutarse sin --authorized")

            result = run_pilot(path, authorized_execution=False, orchestrator_fn=forbidden_orchestrator)
            self.assertEqual(result.status, "FAIL")
            self.assertEqual(result.failed_stage, "authorization_gate")
            self.assertFalse(called["value"])

    def test_local_pipeline_runs_analysis_and_preserves_sealed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = build_pilot_config(
                root / "pilot_local.json", local_acceptance=True, interface_name="eth0"
            )

            result = run_pilot(config_path, orchestrator_fn=self._synthetic_orchestrator)
            self.assertEqual(result.status, "PASS", result.message)
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["stages"]["preflight_capture"], "PASS")
            self.assertEqual(summary["stages"]["analysis_report"], "PASS")
            self.assertEqual(summary["stages"]["integrity_after_report"], "PASS")
            self.assertTrue(summary["integrity_preservation"]["sealed_run_unchanged"])
            self.assertTrue((root / "report" / "report.html").is_file())

    def test_post_report_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = build_pilot_config(
                root / "pilot_local.json", local_acceptance=True, interface_name="eth0"
            )

            def tampering_report(*args, **kwargs):
                result = generate_report(*args, **kwargs)
                with (root / "run" / "interface_samples.csv").open("a", encoding="utf-8") as handle:
                    handle.write("tamper\n")
                return result

            result = run_pilot(
                config_path,
                orchestrator_fn=self._synthetic_orchestrator,
                report_fn=tampering_report,
            )
            self.assertEqual(result.status, "FAIL")
            self.assertEqual(result.failed_stage, "integrity_after_report")


if __name__ == "__main__":
    unittest.main()
