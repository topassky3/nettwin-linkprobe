from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from nettwin.cli import build_parser
from nettwin.correlation_engine import (
    CorrelationConfig,
    analyze_correlations,
    spearman_rho,
    write_correlations,
)


class CorrelationEngineTests(unittest.TestCase):
    def _write_inputs(
        self,
        root: Path,
        *,
        periods: int = 24,
        include_host: bool = True,
        utilization_available: bool = True,
    ) -> tuple[Path, Path, Path | None]:
        timestamps = pd.date_range(
            "2026-08-09T18:00:00+00:00",
            periods=periods,
            freq="5s",
        )
        utilization = np.linspace(20.0, 90.0, periods)

        interface = pd.DataFrame(
            {
                "timestamp": [ts.isoformat() for ts in timestamps],
                "interface": "eth0",
                "utilization_pct": (
                    utilization if utilization_available else [None] * periods
                ),
                "rx_rate_mbps": utilization,
                "tx_rate_mbps": utilization * 0.1,
                "rx_drops_delta": np.floor(
                    np.maximum(utilization - 50.0, 0.0) / 10.0
                ),
                "tx_drops_delta": 0.0,
                "rx_errors_delta": 0.0,
                "tx_errors_delta": 0.0,
                "sample_ok": True,
            }
        )

        probe_rows = []
        for timestamp, util in zip(timestamps, utilization):
            lost = int(max(0.0, (util - 60.0) // 8.0))
            probe_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "target": "external",
                    "packets_sent": 100,
                    "packets_received": 100 - lost,
                    "rtt_ms": 10.0 + util * 0.5,
                    "delay_variation_ms": 1.0 + util * 0.1,
                    "sample_status": "ok",
                }
            )
            probe_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "target": "internal",
                    "packets_sent": 100,
                    "packets_received": 100,
                    "rtt_ms": 5.0,
                    "delay_variation_ms": 1.0,
                    "sample_status": "ok",
                }
            )
        probes = pd.DataFrame(probe_rows)

        interface_path = root / "interface.csv"
        probe_path = root / "probes.csv"
        interface.to_csv(interface_path, index=False)
        probes.to_csv(probe_path, index=False)

        host_path = None
        if include_host:
            cpu = np.tile([20.0, 22.0, 21.0, 23.0, 20.0, 21.0], 8)[:periods]
            host = pd.DataFrame(
                {
                    "timestamp": [ts.isoformat() for ts in timestamps],
                    "cpu_percent": cpu,
                    "memory_percent": 35.0,
                    "load_1m": 0.3,
                    "load_5m": 0.3,
                    "load_15m": 0.3,
                    "uptime_seconds": np.arange(periods) * 5.0 + 3600.0,
                    "sample_status": "ok",
                    "error": "",
                }
            )
            host_path = root / "host.csv"
            host.to_csv(host_path, index=False)

        return interface_path, probe_path, host_path

    def _row(self, result, relation_id: str) -> pd.Series:
        row = result.correlations[
            result.correlations["relation_id"] == relation_id
        ]
        self.assertEqual(len(row), 1)
        return row.iloc[0]

    def test_spearman_handles_monotonic_positive_and_negative_series(self):
        ascending = pd.Series([1, 2, 3, 4, 5])
        descending = pd.Series([5, 4, 3, 2, 1])
        self.assertAlmostEqual(spearman_rho(ascending, ascending), 1.0)
        self.assertAlmostEqual(spearman_rho(ascending, descending), -1.0)

    def test_required_network_relations_are_computed_per_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, host = self._write_inputs(Path(tmp))
            result = analyze_correlations(interface, probes, host_csv=host)

        expected = {
            "utilization__rtt__external",
            "utilization__delay_variation__external",
            "utilization__packet_loss__external",
            "utilization__rtt__internal",
            "utilization__delay_variation__internal",
            "utilization__packet_loss__internal",
            "utilization__drops",
        }
        self.assertTrue(expected.issubset(set(result.correlations["relation_id"])))

        external = self._row(result, "utilization__rtt__external")
        self.assertGreater(external["spearman_rho"], 0.95)
        self.assertEqual(external["strength"], "very_strong")
        self.assertFalse(external["causal_interpretation_allowed"])

    def test_cpu_relations_are_computed_when_host_is_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, host = self._write_inputs(Path(tmp))
            result = analyze_correlations(interface, probes, host_csv=host)

        rtt = self._row(result, "cpu__rtt__external")
        loss = self._row(result, "cpu__packet_loss__external")
        self.assertEqual(rtt["pair_count"], 24)
        self.assertEqual(loss["pair_count"], 24)
        self.assertEqual(rtt["evidence_status"], "weak_association")
        self.assertEqual(loss["evidence_status"], "weak_association")

    def test_constant_target_does_not_invent_correlation(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, host = self._write_inputs(Path(tmp))
            result = analyze_correlations(interface, probes, host_csv=host)

        row = self._row(result, "utilization__rtt__internal")
        self.assertTrue(pd.isna(row["spearman_rho"]))
        self.assertEqual(row["evidence_status"], "constant_series")

    def test_small_sample_is_explicitly_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, host = self._write_inputs(Path(tmp), periods=8)
            result = analyze_correlations(
                interface,
                probes,
                host_csv=host,
                config=CorrelationConfig(min_pairs=12),
            )

        row = self._row(result, "utilization__rtt__external")
        self.assertEqual(row["pair_count"], 8)
        self.assertEqual(row["evidence_status"], "insufficient_samples")
        self.assertEqual(row["descriptive_significance"], "insufficient")

    def test_unknown_capacity_marks_utilization_relations_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, host = self._write_inputs(
                Path(tmp), utilization_available=False
            )
            result = analyze_correlations(interface, probes, host_csv=host)

        row = self._row(result, "utilization__rtt__external")
        self.assertEqual(row["evidence_status"], "unavailable")
        self.assertIn("Utilización N/D", row["note"])
        cpu = self._row(result, "cpu__rtt__external")
        self.assertNotEqual(cpu["evidence_status"], "unavailable")

    def test_missing_host_marks_cpu_relations_unavailable_without_blocking_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, _ = self._write_inputs(Path(tmp), include_host=False)
            result = analyze_correlations(interface, probes)

        cpu = self._row(result, "cpu__rtt__external")
        network = self._row(result, "utilization__rtt__external")
        self.assertEqual(cpu["evidence_status"], "unavailable")
        self.assertEqual(network["evidence_status"], "descriptive_association")

    def test_non_overlapping_host_only_disables_cpu_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface, probes, host = self._write_inputs(root)
            host_df = pd.read_csv(host)
            shifted = pd.to_datetime(host_df["timestamp"], utc=True) + pd.to_timedelta(
                np.full(len(host_df), 3600, dtype="timedelta64[s]")
            )
            host_df["timestamp"] = [value.isoformat() for value in shifted]
            host_df.to_csv(host, index=False)
            result = analyze_correlations(interface, probes, host_csv=host)

        cpu = self._row(result, "cpu__rtt__external")
        network = self._row(result, "utilization__rtt__external")
        self.assertEqual(cpu["evidence_status"], "unavailable")
        self.assertEqual(network["evidence_status"], "descriptive_association")

    def test_non_overlapping_interface_and_probes_disables_joint_network_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface, probes, host = self._write_inputs(root)
            probe_df = pd.read_csv(probes)
            shifted = pd.to_datetime(probe_df["timestamp"], utc=True) + pd.to_timedelta(
                np.full(len(probe_df), 3600, dtype="timedelta64[s]")
            )
            probe_df["timestamp"] = [value.isoformat() for value in shifted]
            probe_df.to_csv(probes, index=False)
            result = analyze_correlations(interface, probes, host_csv=host)

        network = self._row(result, "utilization__rtt__external")
        drops = self._row(result, "utilization__drops")
        self.assertEqual(network["evidence_status"], "unavailable")
        self.assertNotEqual(drops["evidence_status"], "unavailable")

    def test_repeated_analysis_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface, probes, host = self._write_inputs(Path(tmp))
            first = analyze_correlations(interface, probes, host_csv=host)
            second = analyze_correlations(interface, probes, host_csv=host)

        pd.testing.assert_frame_equal(first.correlations, second.correlations)
        pd.testing.assert_frame_equal(first.aligned_pairs, second.aligned_pairs)
        self.assertEqual(first.metadata, second.metadata)

    def test_write_correlations_emits_reproducible_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface, probes, host = self._write_inputs(root)
            result = analyze_correlations(interface, probes, host_csv=host)
            path = write_correlations(result, root / "out")
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue((root / "out" / "correlation_summary.csv").exists())
            self.assertTrue((root / "out" / "correlation_aligned_pairs.csv").exists())
            self.assertEqual(payload["metadata"]["mode"], "correlation-engine")
            self.assertGreater(len(payload["correlations"]), 0)
            self.assertNotIn("NaN", path.read_text(encoding="utf-8"))

    def test_cli_exposes_analyze_correlations(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "analyze-correlations",
                "--interface-csv",
                "interface.csv",
                "--probe-csv",
                "probe.csv",
                "--host-csv",
                "host.csv",
                "--min-pairs",
                "20",
            ]
        )
        self.assertEqual(args.command, "analyze-correlations")
        self.assertEqual(args.host_csv, "host.csv")
        self.assertEqual(args.min_pairs, 20)


if __name__ == "__main__":
    unittest.main()
