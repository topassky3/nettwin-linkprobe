from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nettwin.fingerprint_cli import build_parser
from nettwin.fingerprint_engine import analyze_fingerprints, write_fingerprints


class FingerprintEngineTests(unittest.TestCase):
    def _event(
        self,
        *,
        event_id: str = "EVENT-003",
        include_loss_baseline: bool = True,
    ) -> dict:
        loss = {"target": "external", "loss_pct": 1.3}
        if include_loss_baseline:
            loss["baseline_pct"] = 0.0
        return {
            "event_id": event_id,
            "start": "2026-08-09T17:14:22+00:00",
            "end": "2026-08-09T17:21:48+00:00",
            "duration_seconds": 446.0,
            "severity": "high",
            "confidence": "ALTA",
            "metrics_changed": [
                "utilization",
                "rtt",
                "delay_variation",
                "packet_loss",
                "drops",
            ],
            "evidence": {
                "bucket_seconds": 5,
                "anomalous_buckets": 2,
                "buckets": [
                    {
                        "timestamp": "2026-08-09T17:14:22+00:00",
                        "metrics_changed": [
                            "utilization",
                            "rtt",
                            "delay_variation",
                            "packet_loss",
                            "drops",
                        ],
                        "detail": {
                            "utilization": {
                                "value_pct": 91.0,
                                "baseline_pct": 58.0,
                            },
                            "rtt": [
                                {
                                    "target": "external",
                                    "value_ms": 49.0,
                                    "baseline_ms": 14.0,
                                }
                            ],
                            "delay_variation": [
                                {
                                    "target": "external",
                                    "value_ms": 15.0,
                                    "baseline_ms": 2.0,
                                }
                            ],
                            "packet_loss": [loss],
                            "drops": {"delta": 20.0},
                        },
                    },
                    {
                        "timestamp": "2026-08-09T17:21:43+00:00",
                        "metrics_changed": ["drops"],
                        "detail": {"drops": {"delta": 27.0}},
                    },
                ],
            },
        }

    def _write(self, root: Path, payload: dict) -> Path:
        path = root / "events.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _payload(self, events: list[dict], metadata: dict | None = None) -> dict:
        return {"metadata": metadata or {}, "events": events}

    def test_plan_example_produces_exact_compact_vector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([self._event()]))
            result = analyze_fingerprints(path)
        vector = result.fingerprints[0]["compact_vector"]
        self.assertEqual(vector["delta_utilization_pp"], 33.0)
        self.assertEqual(vector["delta_rtt_pct"], 250.0)
        self.assertEqual(vector["delta_delay_variation_pct"], 650.0)
        self.assertEqual(vector["delta_loss_pp"], 1.3)
        self.assertEqual(vector["delta_drops"], 47.0)
        self.assertEqual(vector["duration_seconds"], 446.0)
        self.assertEqual(vector["core_completeness_pct"], 100.0)

    def test_multi_target_compact_vector_keeps_max_and_target(self):
        event = self._event()
        detail = event["evidence"]["buckets"][0]["detail"]
        detail["rtt"].append(
            {"target": "internal", "value_ms": 9.0, "baseline_ms": 5.0}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([event]))
            result = analyze_fingerprints(path)
        fp = result.fingerprints[0]
        self.assertEqual(fp["compact_vector"]["delta_rtt_target"], "external")
        self.assertIn("external", fp["details"]["rtt_by_target"])
        self.assertIn("internal", fp["details"]["rtt_by_target"])

    def test_missing_loss_baseline_is_nd_without_assuming_zero(self):
        event = self._event(include_loss_baseline=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([event]))
            result = analyze_fingerprints(path)
        fp = result.fingerprints[0]
        vector = fp["compact_vector"]
        self.assertIsNone(vector["delta_loss_pp"])
        self.assertEqual(vector["observed_loss_peak_pct"], 1.3)
        self.assertTrue(any("Δloss N/D" in text for text in fp["limitations"]))

    def test_loss_baseline_can_come_from_event_metadata(self):
        event = self._event(include_loss_baseline=False)
        metadata = {
            "probe_baselines": {
                "external": {
                    "rtt_ms": 14.0,
                    "delay_variation_ms": 2.0,
                    "packet_loss_pct": 0.2,
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([event], metadata))
            result = analyze_fingerprints(path)
        self.assertAlmostEqual(
            result.fingerprints[0]["compact_vector"]["delta_loss_pp"],
            1.1,
        )

    def test_zero_baseline_does_not_invent_percent_delta(self):
        event = self._event()
        event["evidence"]["buckets"][0]["detail"]["rtt"][0]["baseline_ms"] = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([event]))
            result = analyze_fingerprints(path)
        fp = result.fingerprints[0]
        self.assertIsNone(fp["compact_vector"]["delta_rtt_pct"])
        self.assertTrue(any("ΔRTT" in text for text in fp["limitations"]))

    def test_drops_are_summed_across_event_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([self._event()]))
            result = analyze_fingerprints(path)
        self.assertEqual(
            result.fingerprints[0]["details"]["drops_delta_total"],
            47.0,
        )

    def test_no_events_generates_no_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([]))
            result = analyze_fingerprints(path)
        self.assertEqual(result.fingerprints, [])
        self.assertEqual(result.metadata["fingerprint_count"], 0)

    def test_duplicate_event_ids_fail_clearly(self):
        event_a = self._event()
        event_b = self._event()
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([event_a, event_b]))
            with self.assertRaisesRegex(ValueError, "duplicado"):
                analyze_fingerprints(path)

    def test_malformed_event_fails_clearly(self):
        malformed = {"event_id": "EVENT-X"}
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([malformed]))
            with self.assertRaisesRegex(ValueError, "campos requeridos"):
                analyze_fingerprints(path)

    def test_repeated_analysis_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._payload([self._event()]))
            first = analyze_fingerprints(path)
            second = analyze_fingerprints(path)
        self.assertEqual(first.fingerprints, second.fingerprints)
        self.assertEqual(first.metadata, second.metadata)

    def test_write_emits_three_artifacts_and_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write(root, self._payload([self._event()]))
            result = analyze_fingerprints(path)
            json_path = write_fingerprints(result, root / "out")
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                payload["metadata"]["events_source"]["sha256"],
                expected_hash,
            )
            self.assertTrue(
                (root / "out" / "event_fingerprint_summary.csv").exists()
            )
            self.assertTrue(
                (root / "out" / "event_fingerprint_trace.csv").exists()
            )
            self.assertNotIn("NaN", json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                len(payload["fingerprints"][0]["fingerprint_sha256"]),
                64,
            )

    def test_cli_exposes_fingerprint_options(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--events-json",
                "events.json",
                "--output",
                "out",
                "--round-digits",
                "2",
            ]
        )
        self.assertEqual(args.events_json, "events.json")
        self.assertEqual(args.output, "out")
        self.assertEqual(args.round_digits, 2)


if __name__ == "__main__":
    unittest.main()
