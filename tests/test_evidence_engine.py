from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from nettwin.evidence_cli import build_evidence_parser
from nettwin.evidence_engine import EvidenceConfig, analyze_evidence, write_evidence


class EvidenceEngineTests(unittest.TestCase):
    def _event(self, *, target: str = "external", confidence: str = "ALTA", metrics=None):
        metrics = metrics or ["utilization", "rtt", "delay_variation", "packet_loss", "drops"]
        detail = {}
        if "utilization" in metrics:
            detail["utilization"] = {"value_pct": 92.0, "baseline_pct": 30.0}
        if "rtt" in metrics:
            detail["rtt"] = [{"target": target, "value_ms": 55.0, "baseline_ms": 15.0}]
        if "delay_variation" in metrics:
            detail["delay_variation"] = [{"target": target, "value_ms": 22.0, "baseline_ms": 1.0}]
        if "packet_loss" in metrics:
            detail["packet_loss"] = [{"target": target, "loss_pct": 5.0, "reachability": True}]
        if "drops" in metrics:
            detail["drops"] = {"delta": 3.0}
        return {
            "event_id": "EVENT-001",
            "start": "2026-08-09T19:01:00+00:00",
            "end": "2026-08-09T19:01:15+00:00",
            "duration_seconds": 15.0,
            "metrics_changed": metrics,
            "severity": "high",
            "confidence": confidence,
            "evidence": {
                "bucket_seconds": 5,
                "anomalous_buckets": 3,
                "buckets": [{
                    "timestamp": "2026-08-09T19:01:05+00:00",
                    "metrics_changed": metrics,
                    "detail": detail,
                }],
            },
        }

    def _correlation(
        self,
        relation_id: str,
        x_metric: str,
        y_metric: str,
        *,
        target="external",
        rho=0.90,
        status="descriptive_association",
        sufficient=True,
        first="2026-08-09T19:00:00+00:00",
        last="2026-08-09T19:04:00+00:00",
    ):
        return {
            "relation_id": relation_id,
            "x_metric": x_metric,
            "y_metric": y_metric,
            "target": target,
            "pair_count": 48,
            "spearman_rho": rho,
            "abs_rho": abs(rho),
            "direction": "positive" if rho > 0 else "negative",
            "strength": "very_strong" if abs(rho) >= 0.8 else "very_weak",
            "sufficient_samples": sufficient,
            "evidence_status": status,
            "descriptive_significance": "adequate_descriptive",
            "first_timestamp": first,
            "last_timestamp": last,
            "causal_interpretation_allowed": False,
            "note": "Asociación descriptiva; no implica causalidad.",
        }

    def _strong_correlations(self):
        return [
            self._correlation("utilization__rtt__external", "utilization_pct", "rtt_ms", rho=0.94),
            self._correlation(
                "utilization__delay_variation__external",
                "utilization_pct",
                "delay_variation_ms",
                rho=0.89,
            ),
            self._correlation(
                "utilization__packet_loss__external",
                "utilization_pct",
                "packet_loss_pct",
                rho=0.82,
            ),
            self._correlation(
                "utilization__drops",
                "utilization_pct",
                "drops_delta",
                target=None,
                rho=0.78,
            ),
            self._correlation(
                "cpu__rtt__external",
                "cpu_percent",
                "rtt_ms",
                rho=0.05,
                status="weak_association",
            ),
        ]

    def _write(self, root: Path, *, events=None, correlations=None):
        event_path = root / "events.json"
        corr_path = root / "correlations.json"
        event_path.write_text(
            json.dumps({"metadata": {"mode": "event-engine"}, "events": events if events is not None else [self._event()]}),
            encoding="utf-8",
        )
        corr_path.write_text(
            json.dumps({
                "metadata": {"mode": "correlation-engine"},
                "correlations": correlations if correlations is not None else self._strong_correlations(),
            }),
            encoding="utf-8",
        )
        return event_path, corr_path

    def test_required_finding_structure_is_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp))
            result = analyze_evidence(events, correlations)
        finding = result.findings[0]
        for field in ("fact", "interpretation", "hypothesis", "confidence", "recommendation"):
            self.assertTrue(finding[field])
        self.assertEqual(finding["event_id"], "EVENT-001")
        self.assertFalse(finding["causal_claim_allowed"])

    def test_strong_load_correlations_create_high_confidence_queue_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp))
            result = analyze_evidence(events, correlations)
        finding = result.findings[0]
        self.assertEqual(finding["confidence"], "ALTA")
        self.assertIn("colas", finding["hypothesis"])
        self.assertIn("aguas arriba", finding["recommendation"])
        self.assertGreaterEqual(len(finding["evidence"]["supporting_correlations"]), 4)

    def test_weak_correlations_do_not_raise_confidence(self):
        weak = [self._correlation(
            "utilization__rtt__external",
            "utilization_pct",
            "rtt_ms",
            rho=0.12,
            status="weak_association",
        )]
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp), correlations=weak)
            result = analyze_evidence(events, correlations)
        finding = result.findings[0]
        self.assertEqual(finding["confidence"], "MEDIA")
        self.assertEqual(len(finding["evidence"]["supporting_correlations"]), 0)
        self.assertEqual(len(finding["evidence"]["context_correlations"]), 1)

    def test_correlation_outside_event_window_is_not_used_as_support(self):
        outside = [self._correlation(
            "utilization__rtt__external",
            "utilization_pct",
            "rtt_ms",
            first="2026-08-09T20:00:00+00:00",
            last="2026-08-09T20:05:00+00:00",
        )]
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp), correlations=outside)
            result = analyze_evidence(events, correlations)
        self.assertEqual(result.findings[0]["confidence"], "MEDIA")
        self.assertEqual(result.findings[0]["evidence"]["supporting_correlations"], [])

    def test_wrong_target_correlation_is_not_used(self):
        event = self._event(target="external", metrics=["utilization", "rtt"])
        wrong = [self._correlation(
            "utilization__rtt__google",
            "utilization_pct",
            "rtt_ms",
            target="google",
            rho=0.99,
        )]
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp), events=[event], correlations=wrong)
            result = analyze_evidence(events, correlations)
        self.assertEqual(result.findings[0]["evidence"]["supporting_correlations"], [])
        self.assertEqual(result.findings[0]["confidence"], "MEDIA")

    def test_missing_correlations_still_generates_conservative_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events, _ = self._write(root)
            result = analyze_evidence(events)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0]["confidence"], "MEDIA")
        self.assertIsNone(result.metadata["correlations_source"])

    def test_no_events_generates_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp), events=[])
            result = analyze_evidence(events, correlations)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.trace_rows, [])
        self.assertEqual(result.metadata["findings_count"], 0)

    def test_malformed_events_fail_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.json"
            path.write_text(json.dumps({"events": [{"event_id": "EVENT-X"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "campos requeridos"):
                analyze_evidence(path)

    def test_causal_claim_is_explicitly_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp))
            result = analyze_evidence(events, correlations)
        finding = result.findings[0]
        self.assertFalse(finding["causal_claim_allowed"])
        self.assertFalse(result.metadata["causal_inference_performed"])
        combined = " ".join([
            finding["interpretation"], finding["hypothesis"], finding["recommendation"]
        ]).lower()
        self.assertNotIn("causó definitivamente", combined)
        self.assertIn("requiere", combined)

    def test_write_evidence_emits_three_artifacts_and_source_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events, correlations = self._write(root)
            result = analyze_evidence(events, correlations)
            path = write_evidence(result, root / "out")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue((root / "out" / "evidence_summary.csv").exists())
            self.assertTrue((root / "out" / "evidence_trace.csv").exists())
            self.assertEqual(len(payload["metadata"]["events_source"]["sha256"]), 64)
            self.assertEqual(len(payload["metadata"]["correlations_source"]["sha256"]), 64)
            self.assertGreaterEqual(len(result.trace_rows), 2)

    def test_repeated_analysis_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            events, correlations = self._write(Path(tmp))
            first = analyze_evidence(events, correlations)
            second = analyze_evidence(events, correlations)
        self.assertEqual(first.findings, second.findings)
        self.assertEqual(first.trace_rows, second.trace_rows)
        self.assertEqual(first.metadata, second.metadata)

    def test_cli_exposes_analyze_evidence_options(self):
        parser = build_evidence_parser()
        args = parser.parse_args([
            "--events-json", "events.json",
            "--correlations-json", "correlations.json",
            "--min-support-rho", "0.7",
        ])
        self.assertEqual(args.events_json, "events.json")
        self.assertEqual(args.correlations_json, "correlations.json")
        self.assertEqual(args.min_support_rho, 0.7)


if __name__ == "__main__":
    unittest.main()
