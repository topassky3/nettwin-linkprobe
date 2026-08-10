from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from nettwin.event_engine import analyze_events, write_events
from nettwin.fingerprint_engine import analyze_fingerprints


class FingerprintPipelineTests(unittest.TestCase):
    def _write_processed_inputs(self, root: Path) -> tuple[Path, Path]:
        timestamps = pd.date_range("2026-08-09T20:00:00Z", periods=12, freq="5s")
        interface_rows = []
        probe_rows = []

        for index, timestamp in enumerate(timestamps):
            is_event = index in (7, 8)
            interface_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "interface": "eth0",
                    "rx_rate_mbps": 50.0 if is_event else 10.0,
                    "tx_rate_mbps": 2.0,
                    "utilization_pct": 90.0 if is_event else 20.0,
                    "rx_errors_delta": 0,
                    "tx_errors_delta": 0,
                    "rx_drops_delta": 2 if is_event else 0,
                    "tx_drops_delta": 0,
                    "sample_ok": True,
                }
            )

            # Pérdida basal real de 1 %. Durante el segundo bucket del evento sube a 5 %.
            # Esto permite demostrar que Fingerprint Engine calcula +4 pp y NO asume baseline 0.
            received = 95 if index == 8 else 99
            probe_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "target": "external",
                    "packets_sent": 100,
                    "packets_received": received,
                    "rtt_ms": 35.0 if is_event else 10.0,
                    "delay_variation_ms": 20.0 if is_event else 1.0,
                    "reachability_bool": True,
                    "sample_status": "ok",
                }
            )

        interface_path = root / "quality_interface_processed.csv"
        probe_path = root / "quality_probe_processed.csv"
        pd.DataFrame(interface_rows).to_csv(interface_path, index=False)
        pd.DataFrame(probe_rows).to_csv(probe_path, index=False)
        return interface_path, probe_path

    def test_event_engine_exports_observed_packet_loss_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_processed_inputs(Path(tmp))
            result = analyze_events(interface_path, probe_path)

        self.assertEqual(len(result.events), 1)
        self.assertAlmostEqual(
            result.metadata["probe_baselines"]["external"]["packet_loss_pct"],
            1.0,
        )
        self.assertIn("packet_loss_pct", result.metadata["baseline_methodology"])

        loss_rows = []
        for bucket in result.events[0]["evidence"]["buckets"]:
            loss_rows.extend(bucket["detail"].get("packet_loss", []))

        self.assertTrue(loss_rows)
        self.assertTrue(all(row["baseline_pct"] == 1.0 for row in loss_rows))
        self.assertAlmostEqual(max(row["loss_pct"] for row in loss_rows), 5.0)

    def test_event_to_fingerprint_uses_real_nonzero_loss_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface_path, probe_path = self._write_processed_inputs(root)
            event_result = analyze_events(interface_path, probe_path)
            events_path = write_events(event_result, root / "events")

            fingerprint_result = analyze_fingerprints(events_path)

        self.assertEqual(len(fingerprint_result.fingerprints), 1)
        vector = fingerprint_result.fingerprints[0]["compact_vector"]
        self.assertAlmostEqual(vector["delta_loss_pp"], 4.0)
        self.assertAlmostEqual(vector["observed_loss_peak_pct"], 5.0)
        self.assertEqual(vector["delta_loss_target"], "external")
        self.assertFalse(
            fingerprint_result.fingerprints[0]["causal_interpretation_allowed"]
        )


if __name__ == "__main__":
    unittest.main()
