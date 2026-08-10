from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from nettwin.cli import build_parser
from nettwin.event_engine import EventThresholds, analyze_events, write_events


class EventEngineTests(unittest.TestCase):
    def _write_inputs(
        self,
        root: Path,
        *,
        event: bool = True,
        capacity_known: bool = True,
        overlap: bool = True,
        single_metric_only: bool = False,
        include_slow_target: bool = False,
    ) -> tuple[Path, Path]:
        timestamps = pd.date_range("2026-08-09T16:00:00Z", periods=12, freq="5s")
        interface_rows = []
        for index, timestamp in enumerate(timestamps):
            rx_rate = 10.0
            tx_rate = 2.0
            utilization = 20.0 if capacity_known else None
            drops = 0
            if event and index in (7, 8):
                rx_rate = 50.0
                utilization = 90.0 if capacity_known else None
                if not single_metric_only:
                    drops = 2
            interface_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "interface": "eth0",
                    "rx_rate_mbps": rx_rate,
                    "tx_rate_mbps": tx_rate,
                    "utilization_pct": utilization,
                    "rx_errors_delta": 0,
                    "tx_errors_delta": 0,
                    "rx_drops_delta": drops,
                    "tx_drops_delta": 0,
                    "sample_ok": True,
                }
            )

        probe_timestamps = timestamps if overlap else timestamps + timedelta(minutes=30)
        probe_rows = []
        for index, timestamp in enumerate(probe_timestamps):
            rtt = 10.0
            delay = 1.0
            received = 1
            if event and not single_metric_only and index in (7, 8):
                rtt = 35.0
                delay = 20.0
                if index == 8:
                    received = 0
            probe_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "target": "external",
                    "packets_sent": 1,
                    "packets_received": received,
                    "rtt_ms": rtt if received else None,
                    "delay_variation_ms": delay if received else None,
                    "reachability_bool": bool(received),
                    "sample_status": "ok",
                }
            )
            if include_slow_target:
                probe_rows.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "target": "slow-reference",
                        "packets_sent": 1,
                        "packets_received": 1,
                        "rtt_ms": 100.0,
                        "delay_variation_ms": 1.0,
                        "reachability_bool": True,
                        "sample_status": "ok",
                    }
                )

        interface_path = root / "quality_interface_processed.csv"
        probe_path = root / "quality_probe_processed.csv"
        pd.DataFrame(interface_rows).to_csv(interface_path, index=False)
        pd.DataFrame(probe_rows).to_csv(probe_path, index=False)
        return interface_path, probe_path

    def test_detects_multimetric_event_and_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            result = analyze_events(interface_path, probe_path)
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        for field in (
            "event_id", "start", "end", "duration_seconds", "metrics_changed",
            "severity", "evidence", "interpretation", "hypothesis", "confidence",
        ):
            self.assertIn(field, event)
        self.assertEqual(event["event_id"], "EVENT-001")
        self.assertIn("utilization", event["metrics_changed"])
        self.assertIn("rtt", event["metrics_changed"])
        self.assertIn("delay_variation", event["metrics_changed"])
        self.assertIn("packet_loss", event["metrics_changed"])
        self.assertIn("drops", event["metrics_changed"])

    def test_adjacent_candidate_buckets_are_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            result = analyze_events(interface_path, probe_path)
        event = result.events[0]
        self.assertEqual(event["evidence"]["anomalous_buckets"], 2)
        self.assertAlmostEqual(event["duration_seconds"], 10.0)
        self.assertEqual(result.metadata["candidate_buckets"], 2)

    def test_single_metric_spike_is_not_event_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp), single_metric_only=True)
            result = analyze_events(interface_path, probe_path)
        self.assertEqual(result.events, [])
        self.assertEqual(result.metadata["event_count"], 0)

    def test_no_overlap_skips_joint_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp), overlap=False)
            result = analyze_events(interface_path, probe_path)
        self.assertFalse(result.metadata["temporal_overlap"]["exists"])
        self.assertFalse(result.metadata["event_detection_executed"])
        self.assertEqual(result.events, [])
        self.assertTrue(any("solapamiento" in item for item in result.metadata["limitations"]))

    def test_unknown_capacity_uses_relative_traffic_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp), capacity_known=False)
            result = analyze_events(interface_path, probe_path)
        self.assertEqual(len(result.events), 1)
        self.assertIn("traffic_rate", result.events[0]["metrics_changed"])
        self.assertNotIn("utilization", result.events[0]["metrics_changed"])

    def test_rtt_baseline_is_per_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp), include_slow_target=True)
            result = analyze_events(interface_path, probe_path)
        self.assertEqual(len(result.events), 1)
        rtt_details = []
        for bucket in result.events[0]["evidence"]["buckets"]:
            rtt_details.extend(bucket["detail"].get("rtt", []))
        self.assertTrue(any(item["target"] == "external" for item in rtt_details))
        self.assertFalse(any(item["target"] == "slow-reference" for item in rtt_details))

    def test_minimum_metrics_is_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp), single_metric_only=True)
            result = analyze_events(
                interface_path,
                probe_path,
                thresholds=EventThresholds(min_metrics_changed=1),
            )
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["metrics_changed"], ["utilization"])

    def test_invalid_threshold_configuration_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            with self.assertRaisesRegex(ValueError, "merge_gap_seconds"):
                analyze_events(
                    interface_path,
                    probe_path,
                    thresholds=EventThresholds(bucket_seconds=10, merge_gap_seconds=5),
                )

    def test_write_events_emits_json_timeline_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interface_path, probe_path = self._write_inputs(root)
            result = analyze_events(interface_path, probe_path)
            events_path = write_events(result, root / "out")
            payload = json.loads(events_path.read_text(encoding="utf-8"))
            self.assertTrue((root / "out" / "event_timeline.csv").exists())
            self.assertTrue((root / "out" / "event_summary.csv").exists())
        self.assertEqual(payload["metadata"]["event_count"], 1)
        self.assertEqual(payload["events"][0]["event_id"], "EVENT-001")

    def test_repeated_analysis_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            interface_path, probe_path = self._write_inputs(Path(tmp))
            first = analyze_events(interface_path, probe_path)
            second = analyze_events(interface_path, probe_path)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.metadata["event_count"], second.metadata["event_count"])

    def test_cli_exposes_analyze_events_and_thresholds(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "analyze-events",
                "--interface-csv", "interface.csv",
                "--probe-csv", "probe.csv",
                "--min-metrics", "3",
                "--bucket-seconds", "10",
                "--merge-gap-seconds", "20",
            ]
        )
        self.assertEqual(args.command, "analyze-events")
        self.assertEqual(args.min_metrics, 3)
        self.assertEqual(args.bucket_seconds, 10)
        self.assertEqual(args.merge_gap_seconds, 20)


if __name__ == "__main__":
    unittest.main()
