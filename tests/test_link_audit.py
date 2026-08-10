from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from nettwin.cli import build_parser, main
from nettwin.link_audit import analyze_link_audit, write_link_audit


class LinkAuditTests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        ts = pd.date_range("2026-08-09 14:00:00", periods=49, freq="5min")
        raw = pd.DataFrame(
            {
                "timestamp": ts,
                "link_id": ["LINK-01"] * len(ts),
                "rx_mbps": [20 + (i % 10) * 4 for i in range(len(ts))],
                "tx_mbps": [10 + (i % 6) * 2 for i in range(len(ts))],
                "capacity_mbps": [100] * len(ts),
                "latency_ms": [12 + (i % 8) for i in range(len(ts))],
                "packet_loss_pct": [0.0 if i % 12 else 0.4 for i in range(len(ts))],
                "availability_pct": [100.0] * len(ts),
            }
        )
        raw["rx_utilization"] = raw["rx_mbps"] / raw["capacity_mbps"]
        raw["tx_utilization"] = raw["tx_mbps"] / raw["capacity_mbps"]
        raw["traffic_mbps"] = raw[["rx_mbps", "tx_mbps"]].max(axis=1)
        raw["utilization"] = raw[["rx_utilization", "tx_utilization"]].max(axis=1)
        raw["bottleneck_direction"] = "RX"
        raw["over_capacity"] = False
        raw["date"] = raw["timestamp"].dt.floor("D")
        raw["hour"] = raw["timestamp"].dt.hour
        return raw

    def test_parser_exposes_separate_modes(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["analyze-history", "x.csv"]).command, "analyze-history")
        self.assertEqual(parser.parse_args(["link-audit", "x.csv"]).command, "link-audit")

    def test_link_audit_has_no_long_term_projection_fields(self):
        result = analyze_link_audit(self._frame())
        forbidden = {
            "days_to_critical",
            "trend_direction",
            "trend_pct_points_per_day",
            "recommended_capacity_mbps",
            "technical_required_capacity_mbps",
        }
        self.assertTrue(forbidden.isdisjoint(result.summary.columns))
        self.assertFalse(result.metadata["long_term_projections_enabled"])
        self.assertFalse(result.metadata["trend_projection_enabled"])
        self.assertFalse(result.metadata["capacity_forecast_enabled"])
        self.assertAlmostEqual(float(result.summary.iloc[0]["duration_minutes"]), 240.0)

    def test_link_audit_writes_manifest_and_summary(self):
        result = analyze_link_audit(self._frame())
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_link_audit(result, tmp)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "link-audit")
            self.assertFalse(payload["long_term_projections_enabled"])
            self.assertTrue((Path(tmp) / "link_audit_summary.csv").exists())
            self.assertTrue((Path(tmp) / "link_audit_processed.csv").exists())

    def test_cli_link_audit_on_four_hour_csv(self):
        frame = self._frame()[[
            "timestamp", "link_id", "rx_mbps", "tx_mbps", "capacity_mbps",
            "latency_ms", "packet_loss_pct", "availability_pct",
        ]]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "four_hours.csv"
            out = Path(tmp) / "out"
            frame.to_csv(csv_path, index=False)
            code = main(["link-audit", str(csv_path), "--output", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads((out / "link_audit.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(float(payload["duration_minutes"]), 240.0)
            self.assertFalse(payload["long_term_projections_enabled"])


if __name__ == "__main__":
    unittest.main()
