from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from nettwin.cli import build_parser
from nettwin.quality_engine import analyze_quality, write_quality


class QualityEngineTests(unittest.TestCase):
    def _write_inputs(self, root: Path) -> tuple[Path, Path]:
        interface = pd.DataFrame(
            [
                {
                    "timestamp": "2026-08-09T16:00:00+00:00", "interface": "eth0",
                    "rx_bytes_delta": None, "tx_bytes_delta": None,
                    "rx_errors_delta": None, "tx_errors_delta": None,
                    "rx_drops_delta": None, "tx_drops_delta": None,
                    "sample_status": "ok", "reported_link_speed_mbps": 1000,
                },
                {
                    "timestamp": "2026-08-09T16:00:10+00:00", "interface": "eth0",
                    "rx_bytes_delta": 10_000_000, "tx_bytes_delta": 5_000_000,
                    "rx_errors_delta": 1, "tx_errors_delta": 0,
                    "rx_drops_delta": 2, "tx_drops_delta": 0,
                    "sample_status": "ok", "reported_link_speed_mbps": 1000,
                },
                {
                    "timestamp": "2026-08-09T16:00:20+00:00", "interface": "eth0",
                    "rx_bytes_delta": 20_000_000, "tx_bytes_delta": 10_000_000,
                    "rx_errors_delta": 0, "tx_errors_delta": 1,
                    "rx_drops_delta": 0, "tx_drops_delta": 3,
                    "sample_status": "ok", "reported_link_speed_mbps": 1000,
                },
            ]
        )
        probes = pd.DataFrame(
            [
                {"timestamp": "2026-08-09T16:00:00+00:00", "target": "external", "address": "1.1.1.1", "packets_sent": 1, "packets_received": 1, "packet_loss_pct": 0, "reachability": True, "rtt_ms": 10, "rtt_min_ms": 10, "rtt_max_ms": 10, "delay_variation_ms": None},
                {"timestamp": "2026-08-09T16:00:05+00:00", "target": "external", "address": "1.1.1.1", "packets_sent": 1, "packets_received": 1, "packet_loss_pct": 0, "reachability": True, "rtt_ms": 20, "rtt_min_ms": 20, "rtt_max_ms": 20, "delay_variation_ms": 10},
                {"timestamp": "2026-08-09T16:00:10+00:00", "target": "external", "address": "1.1.1.1", "packets_sent": 1, "packets_received": 0, "packet_loss_pct": 100, "reachability": False, "rtt_ms": None, "rtt_min_ms": None, "rtt_max_ms": None, "delay_variation_ms": None},
                {"timestamp": "2026-08-09T16:00:15+00:00", "target": "external", "address": "1.1.1.1", "packets_sent": 1, "packets_received": 1, "packet_loss_pct": 0, "reachability": True, "rtt_ms": 30, "rtt_min_ms": 30, "rtt_max_ms": 30, "delay_variation_ms": 10},
            ]
        )
        interface_path = root / "interface.csv"
        probe_path = root / "probes.csv"
        interface.to_csv(interface_path, index=False)
        probes.to_csv(probe_path, index=False)
        return interface_path, probe_path

    def test_interface_rates_and_utilization_use_explicit_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            result = analyze_quality(interface_path, probe_path, capacity_mbps=20)
        row = result.interface_summary.iloc[0]
        self.assertAlmostEqual(row["rx_rate_mean_mbps"], 12.0)
        self.assertAlmostEqual(row["tx_rate_mean_mbps"], 6.0)
        self.assertAlmostEqual(row["utilization_mean_pct"], 60.0)
        self.assertAlmostEqual(row["utilization_p95_pct"], 78.0)
        self.assertAlmostEqual(row["utilization_max_pct"], 80.0)
        self.assertEqual(row["rx_errors_delta_total"], 1)
        self.assertEqual(row["tx_errors_delta_total"], 1)
        self.assertEqual(row["rx_drops_delta_total"], 2)
        self.assertEqual(row["tx_drops_delta_total"], 3)

    def test_rate_spans_from_previous_valid_sample_after_transient_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface_path, probe_path = self._write_inputs(root)
            transient = pd.DataFrame(
                [
                    {
                        "timestamp": "2026-08-09T16:00:00+00:00", "interface": "eth0",
                        "rx_bytes_delta": None, "tx_bytes_delta": None,
                        "rx_errors_delta": None, "tx_errors_delta": None,
                        "rx_drops_delta": None, "tx_drops_delta": None,
                        "sample_status": "ok", "reported_link_speed_mbps": 1000,
                    },
                    {
                        "timestamp": "2026-08-09T16:00:10+00:00", "interface": "eth0",
                        "rx_bytes_delta": None, "tx_bytes_delta": None,
                        "rx_errors_delta": None, "tx_errors_delta": None,
                        "rx_drops_delta": None, "tx_drops_delta": None,
                        "sample_status": "error", "reported_link_speed_mbps": None,
                    },
                    {
                        "timestamp": "2026-08-09T16:00:20+00:00", "interface": "eth0",
                        "rx_bytes_delta": 20_000_000, "tx_bytes_delta": 10_000_000,
                        "rx_errors_delta": 0, "tx_errors_delta": 0,
                        "rx_drops_delta": 0, "tx_drops_delta": 0,
                        "sample_status": "ok", "reported_link_speed_mbps": 1000,
                    },
                ]
            )
            transient.to_csv(interface_path, index=False)
            result = analyze_quality(interface_path, probe_path, capacity_mbps=20)
        recovered_row = result.interface_processed.iloc[2]
        self.assertAlmostEqual(recovered_row["elapsed_seconds"], 20.0)
        self.assertAlmostEqual(recovered_row["rx_rate_mbps"], 8.0)
        self.assertAlmostEqual(recovered_row["tx_rate_mbps"], 4.0)

    def test_nic_reported_speed_is_not_used_as_link_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            result = analyze_quality(interface_path, probe_path)
        row = result.interface_summary.iloc[0]
        self.assertIsNone(row["capacity_mbps"])
        self.assertTrue(pd.isna(row["utilization_mean_pct"]))
        self.assertIn("no se usa como capacidad", result.metadata["limitations"][1])

    def test_probe_summary_computes_loss_availability_and_rtt_percentiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            result = analyze_quality(interface_path, probe_path, capacity_mbps=20)
        row = result.probe_summary.iloc[0]
        self.assertEqual(row["total_probes"], 4)
        self.assertEqual(row["successful_probes"], 3)
        self.assertEqual(row["lost_probes"], 1)
        self.assertAlmostEqual(row["loss_percent"], 25.0)
        self.assertAlmostEqual(row["availability_observed_pct"], 75.0)
        self.assertAlmostEqual(row["rtt_min_ms"], 10.0)
        self.assertAlmostEqual(row["rtt_median_ms"], 20.0)
        self.assertAlmostEqual(row["rtt_p95_ms"], 29.0)
        self.assertAlmostEqual(row["rtt_p99_ms"], 29.8)
        self.assertAlmostEqual(row["rtt_max_ms"], 30.0)
        self.assertAlmostEqual(row["delay_variation_p95_ms"], 10.0)

    def test_metadata_limits_availability_to_observed_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            result = analyze_quality(interface_path, probe_path, capacity_mbps=20)
        self.assertEqual(result.metadata["scope"], "observed_window_only")
        self.assertIn("ventana observada", result.metadata["availability_definition"])
        self.assertIn("no es jitter unidireccional", result.metadata["delay_variation_definition"])
        self.assertTrue(result.metadata["temporal_overlap"]["exists"])
        self.assertAlmostEqual(result.metadata["temporal_overlap"]["duration_seconds"], 15.0)
        self.assertAlmostEqual(result.metadata["duration_seconds"], 15.0)

    def test_non_overlapping_inputs_are_explicitly_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface_path, probe_path = self._write_inputs(root)
            probes = pd.read_csv(probe_path)
            probes["timestamp"] = [
                "2026-08-09T16:30:00+00:00",
                "2026-08-09T16:30:05+00:00",
                "2026-08-09T16:30:10+00:00",
                "2026-08-09T16:30:15+00:00",
            ]
            probes.to_csv(probe_path, index=False)
            result = analyze_quality(interface_path, probe_path, capacity_mbps=20)
        overlap = result.metadata["temporal_overlap"]
        self.assertFalse(overlap["exists"])
        self.assertEqual(overlap["duration_seconds"], 0.0)
        self.assertEqual(result.metadata["duration_seconds"], 0.0)
        self.assertGreater(result.metadata["union_span_seconds"], 0.0)
        self.assertTrue(any("no se solapan" in item for item in result.metadata["limitations"]))

    def test_write_quality_emits_reproducible_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface_path, probe_path = self._write_inputs(root)
            result = analyze_quality(interface_path, probe_path, capacity_mbps=20)
            manifest = write_quality(result, root / "out")
            self.assertTrue(manifest.exists())
            self.assertTrue((root / "out" / "quality_interface_summary.csv").exists())
            self.assertTrue((root / "out" / "quality_probe_summary.csv").exists())
            self.assertTrue((root / "out" / "quality_interface_processed.csv").exists())
            self.assertTrue((root / "out" / "quality_probe_processed.csv").exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["metadata"]["mode"], "quality-engine")
        self.assertEqual(len(payload["interfaces"]), 1)
        self.assertEqual(len(payload["targets"]), 1)

    def test_invalid_capacity_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            with self.assertRaisesRegex(ValueError, "capacity_mbps debe ser > 0"):
                analyze_quality(interface_path, probe_path, capacity_mbps=0)

    def test_cli_exposes_analyze_quality(self):
        parser = build_parser()
        args = parser.parse_args([
            "analyze-quality", "--interface-csv", "interface.csv", "--probe-csv", "probe.csv"
        ])
        self.assertEqual(args.command, "analyze-quality")
        self.assertEqual(args.interface_csv, "interface.csv")
        self.assertEqual(args.probe_csv, "probe.csv")


if __name__ == "__main__":
    unittest.main()
