from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from nettwin.active_probe import ProbeSample
from nettwin.host_collector import HostSample
from nettwin.interface_collector import InterfaceSample
from nettwin.orchestrator_cli import build_parser
from nettwin.orchestrator_engine import CollectorStats, _status_for_run, run_linkprobe


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeInterfaceCollector:
    def __init__(self, interface: str, *, first_error: bool = False) -> None:
        self.interface = interface
        self.counter = 0
        self.first_error = first_error

    def sample(self) -> InterfaceSample:
        self.counter += 1
        if self.first_error and self.counter == 1:
            return InterfaceSample(
                timestamp=_now(), interface=self.interface,
                rx_bytes=None, tx_bytes=None, rx_packets=None, tx_packets=None,
                rx_errors=None, tx_errors=None, rx_drops=None, tx_drops=None,
                interface_state="UNKNOWN", reported_link_speed_mbps=None, mtu=None,
                counter_event="unavailable", sample_status="error", error="lectura temporal",
            )
        base = self.counter * 1000
        return InterfaceSample(
            timestamp=_now(), interface=self.interface,
            rx_bytes=base, tx_bytes=base + 100,
            rx_packets=self.counter * 10, tx_packets=self.counter * 11,
            rx_errors=0, tx_errors=0, rx_drops=0, tx_drops=0,
            interface_state="UP", reported_link_speed_mbps=1000, mtu=1500,
            rx_bytes_delta=None if self.counter == 1 else 1000,
            tx_bytes_delta=None if self.counter == 1 else 1000,
            counter_event="initial" if self.counter == 1 else "normal",
        )


class FatalInterfaceCollector:
    def __init__(self, interface: str) -> None:
        self.interface = interface

    def sample(self) -> InterfaceSample:
        raise RuntimeError("fallo fatal simulado")


class FakeHostCollector:
    def __init__(self) -> None:
        self.counter = 0

    def sample(self) -> HostSample:
        self.counter += 1
        return HostSample(
            timestamp=_now(), cpu_percent=10.0 + self.counter,
            memory_percent=40.0, load_1m=0.2, load_5m=0.2,
            load_15m=0.2, uptime_seconds=1000.0 + self.counter,
        )


class FakeProbeEngine:
    def __init__(self, *, reachable: bool = True, error: bool = False) -> None:
        self.reachable = reachable
        self.error = error
        self.counter = 0

    def probe_all(self, targets):
        self.counter += 1
        rows = []
        for target in targets:
            rows.append(
                ProbeSample(
                    timestamp=_now(), target=target.name, address=target.address,
                    probe_type="icmp_echo", packets_sent=1,
                    packets_received=1 if self.reachable else 0,
                    packet_loss_pct=0.0 if self.reachable else 100.0,
                    reachability=self.reachable,
                    rtt_ms=1.0 if self.reachable else None,
                    rtt_min_ms=1.0 if self.reachable else None,
                    rtt_max_ms=1.0 if self.reachable else None,
                    delay_variation_ms=0.0 if self.counter > 1 and self.reachable else None,
                    payload_bytes=32, estimated_outbound_payload_bytes=32,
                    sample_status="error" if self.error else "ok",
                    error="probe simulado" if self.error else "",
                )
            )
        return rows


class OrchestratorTests(unittest.TestCase):
    def _config(
        self,
        root: Path,
        *,
        duration: float = 0.11,
        interface_interval: float = 0.04,
        host_interval: float = 0.04,
        probe_interval: float = 0.04,
        targets: int = 1,
    ) -> Path:
        addresses = ["127.0.0.1", "192.0.2.1", "198.51.100.1"]
        payload = {
            "run": {"run_id": "TEST-ORCH-001", "duration_seconds": duration},
            "interface": {
                "name": "eth0", "interval_seconds": interface_interval,
                "capacity_mbps": None,
            },
            "host": {"interval_seconds": host_interval},
            "probes": {
                "interval_seconds": probe_interval,
                "timeout_ms": 1000,
                "payload_bytes": 32,
                "count_per_target": 1,
            },
            "targets": [
                {"name": f"target-{index+1}", "address": addresses[index], "role": "test"}
                for index in range(targets)
            ],
            "output": {"directory": "./run", "minimum_free_disk_mb": 1},
        }
        path = root / "config.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _preflight(self, ready: bool = True, capture: dict | None = None):
        def fake(config_path, *, output_path, **kwargs):
            if capture is not None:
                capture.update({"config_path": str(config_path), **kwargs})
            payload = {
                "status": "PASS" if ready else "FAIL",
                "ready_for_run": ready,
                "scope": "observational_only",
            }
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload), encoding="utf-8")
            return SimpleNamespace(payload=payload, output_path=output)
        return fake

    def _run(self, config_path: Path, **kwargs):
        defaults = dict(
            sensor_version="0.3-dev",
            install_signal_handlers=False,
            preflight_fn=self._preflight(True),
            interface_collector_factory=lambda name: FakeInterfaceCollector(name),
            host_collector_factory=lambda: FakeHostCollector(),
            probe_engine_factory=lambda: FakeProbeEngine(),
        )
        defaults.update(kwargs)
        return run_linkprobe(config_path, **defaults)

    def test_cli_exposes_run_linkprobe_options(self):
        args = build_parser().parse_args([
            "--config", "config.json", "--sensor-version", "0.3-dev",
            "--include-hostname", "--redact-local-addresses",
        ])
        self.assertEqual(args.config, "config.json")
        self.assertEqual(args.sensor_version, "0.3-dev")
        self.assertTrue(args.include_hostname)
        self.assertTrue(args.redact_local_addresses)

    def test_successful_run_writes_raw_artifacts_metadata_checksums_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(self._config(root))
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.stop_reason, "duration_elapsed")
            self.assertTrue(result.integrity_valid)
            run_dir = root / "run"
            for relative in (
                "preflight.json", "experiment_config.json", "interface_samples.csv",
                "host_samples.csv", "probe_samples.csv", "orchestration.json",
                "logs/linkprobe.log", "run_metadata.json", "checksums.sha256",
                "integrity_report.json",
            ):
                self.assertTrue((run_dir / relative).exists(), relative)
            orchestration = json.loads((run_dir / "orchestration.json").read_text(encoding="utf-8"))
            self.assertTrue(orchestration["automatic_execution"])
            self.assertTrue(orchestration["integrity"]["strict_verification_requested"])
            self.assertFalse(orchestration["network_safety"]["payload_capture"])

    def test_collectors_share_window_but_keep_independent_intervals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(self._config(
                root, duration=0.11, interface_interval=0.03,
                host_interval=0.05, probe_interval=0.07,
            ))
            payload = json.loads(result.orchestration_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["collectors"]["interface"]["cycles"], 4)
            self.assertEqual(payload["collectors"]["host"]["cycles"], 3)
            self.assertEqual(payload["collectors"]["probes"]["cycles"], 2)
            self.assertEqual(payload["status"], "completed")

    def test_probe_rows_are_per_target_without_stopping_other_collectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(
                self._config(root, duration=0.09, targets=2),
                probe_engine_factory=lambda: FakeProbeEngine(reachable=False),
            )
            payload = json.loads(result.orchestration_path.read_text(encoding="utf-8"))
            probe_stats = payload["collectors"]["probes"]
            self.assertEqual(probe_stats["rows"], probe_stats["cycles"] * 2)
            self.assertGreater(payload["collectors"]["interface"]["rows"], 0)
            self.assertGreater(payload["collectors"]["host"]["rows"], 0)
            self.assertEqual(result.status, "completed")

    def test_sample_error_is_preserved_and_run_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(
                self._config(root),
                interface_collector_factory=lambda name: FakeInterfaceCollector(name, first_error=True),
            )
            payload = json.loads(result.orchestration_path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "completed_with_sample_errors")
            self.assertEqual(payload["collectors"]["interface"]["error_rows"], 1)
            self.assertGreater(payload["collectors"]["host"]["rows"], 0)
            self.assertGreater(payload["collectors"]["probes"]["rows"], 0)
            self.assertTrue(result.integrity_valid)

    def test_fatal_collector_failure_marks_partial_failure_without_killing_peers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(
                self._config(root),
                interface_collector_factory=lambda name: FatalInterfaceCollector(name),
            )
            payload = json.loads(result.orchestration_path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "partial_failure")
            self.assertIn("fallo fatal simulado", payload["collectors"]["interface"]["fatal_error"])
            self.assertGreater(payload["collectors"]["host"]["rows"], 0)
            self.assertGreater(payload["collectors"]["probes"]["rows"], 0)
            self.assertTrue(result.integrity_valid)

    def test_failed_preflight_blocks_active_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_linkprobe(
                self._config(root), sensor_version="0.3-dev",
                install_signal_handlers=False, preflight_fn=self._preflight(False),
                interface_collector_factory=lambda name: (_ for _ in ()).throw(AssertionError("no debe iniciar")),
                host_collector_factory=lambda: (_ for _ in ()).throw(AssertionError("no debe iniciar")),
                probe_engine_factory=lambda: (_ for _ in ()).throw(AssertionError("no debe iniciar")),
            )
            self.assertEqual(result.status, "preflight_blocked")
            self.assertFalse(result.preflight_ready)
            self.assertIsNone(result.integrity_valid)
            self.assertFalse((root / "run" / "interface_samples.csv").exists())
            self.assertFalse((root / "run" / "checksums.sha256").exists())

    def test_existing_run_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._config(root)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "evidence.txt").write_text("preservar", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no sobrescribir evidencia"):
                self._run(config)
            self.assertEqual((run_dir / "evidence.txt").read_text(encoding="utf-8"), "preservar")

    def test_config_is_copied_byte_for_byte_and_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._config(root)
            original = config.read_bytes()
            result = self._run(config)
            copied = (result.run_dir / "experiment_config.json").read_bytes()
            self.assertEqual(copied, original)
            manifest = (result.run_dir / "checksums.sha256").read_text(encoding="utf-8")
            self.assertIn("experiment_config.json", manifest)
            self.assertIn("orchestration.json", manifest)
            self.assertIn("logs/linkprobe.log", manifest)

    def test_target_addresses_are_not_written_to_operational_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._config(root)
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload["targets"][0]["address"] = "203.0.113.77"
            payload["targets"][0]["name"] = "authorized-test-target"
            config.write_text(json.dumps(payload), encoding="utf-8")
            result = self._run(config)
            log = (result.run_dir / "logs" / "linkprobe.log").read_text(encoding="utf-8")
            self.assertIn("authorized-test-target", log)
            self.assertNotIn("203.0.113.77", log)

    def test_preflight_privacy_flags_are_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = {}
            self._run(
                self._config(root),
                include_hostname=True,
                redact_local_addresses=True,
                preflight_fn=self._preflight(True, capture),
            )
            self.assertTrue(capture["include_hostname"])
            self.assertTrue(capture["redact_local_addresses"])
            self.assertEqual(capture["sensor_version"], "0.3-dev")

    def test_integrity_report_is_strict_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(self._config(root))
            report = json.loads(result.integrity_report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["valid"])
            self.assertTrue(report["strict_untracked"])
            self.assertEqual(report["untracked_files"], [])
            self.assertEqual(report["missing_files"], [])
            self.assertEqual(report["mismatched_files"], [])

    def test_signal_stop_reason_maps_to_interrupted(self):
        stats = [CollectorStats("interface", 5), CollectorStats("host", 5), CollectorStats("probes", 5)]
        self.assertEqual(_status_for_run("signal:SIGINT", stats), "interrupted")


if __name__ == "__main__":
    unittest.main()
