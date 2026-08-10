from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from nettwin.analytics import _safe_spearman, analyze
from nettwin.config import AnalysisConfig


def processed_frame(
    timestamps: pd.DatetimeIndex,
    link_id: str,
    rx,
    tx,
    capacity: float,
    latency=None,
    loss=None,
) -> pd.DataFrame:
    n = len(timestamps)
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "link_id": [link_id] * n,
            "rx_mbps": np.broadcast_to(rx, n).astype(float),
            "tx_mbps": np.broadcast_to(tx, n).astype(float),
            "capacity_mbps": [capacity] * n,
        }
    )
    if latency is not None:
        df["latency_ms"] = np.broadcast_to(latency, n).astype(float)
    if loss is not None:
        df["packet_loss_pct"] = np.broadcast_to(loss, n).astype(float)
    df["rx_utilization"] = df["rx_mbps"] / df["capacity_mbps"]
    df["tx_utilization"] = df["tx_mbps"] / df["capacity_mbps"]
    df["traffic_mbps"] = df[["rx_mbps", "tx_mbps"]].max(axis=1)
    df["utilization"] = df[["rx_utilization", "tx_utilization"]].max(axis=1)
    df["bottleneck_direction"] = np.where(df["rx_utilization"] >= df["tx_utilization"], "RX", "TX")
    df["over_capacity"] = df["utilization"] > 1
    df["date"] = df["timestamp"].dt.floor("D")
    df["hour"] = df["timestamp"].dt.hour
    return df


class AnalyticsTests(unittest.TestCase):
    def test_critical_link_is_classified(self):
        timestamps = pd.date_range("2026-07-01", periods=96, freq="15min")
        df = processed_frame(timestamps, "TEST_CRITICAL", 90.0, 40.0, 100.0)
        result = analyze(df, AnalysisConfig())
        row = result.summary.iloc[0]
        self.assertEqual(row["status"], "CRÍTICO")
        self.assertGreaterEqual(row["recommended_capacity_mbps"], 150)

    def test_optional_quality_columns_are_not_required(self):
        timestamps = pd.date_range("2026-07-01", periods=288, freq="15min")
        traffic = np.array([20 + (i % 96) / 10 for i in range(len(timestamps))])
        df = processed_frame(timestamps, "TEST_OK", traffic, 10.0, 100.0)
        result = analyze(df, AnalysisConfig())
        self.assertEqual(len(result.summary), 1)
        self.assertEqual(result.summary.iloc[0]["quality_load_association"], "SIN_DATOS")

    def test_tx_bottleneck_is_reported_separately(self):
        timestamps = pd.date_range("2026-07-01", periods=96, freq="15min")
        df = processed_frame(timestamps, "TEST_TX", 20.0, 80.0, 100.0)
        row = analyze(df, AnalysisConfig()).summary.iloc[0]
        self.assertEqual(row["dominant_direction"], "TX")
        self.assertGreater(row["tx_p95_utilization_pct"], row["rx_p95_utilization_pct"])

    def test_reliable_growth_generates_projection(self):
        timestamps = pd.date_range("2026-07-01", periods=14 * 4, freq="6h")
        days = np.repeat(np.arange(14), 4)
        rx = 50 + days * 1.0
        df = processed_frame(timestamps, "TEST_GROWTH", rx, 20.0, 100.0)
        row = analyze(df, AnalysisConfig()).summary.iloc[0]
        self.assertTrue(bool(row["trend_reliable"]))
        self.assertIn(row["trend_confidence"], {"MEDIA", "ALTA"})
        self.assertFalse(pd.isna(row["days_to_critical"]))
        self.assertGreater(row["trend_r2"], 0.9)

    def test_flat_series_does_not_invent_saturation_date(self):
        timestamps = pd.date_range("2026-07-01", periods=14 * 4, freq="6h")
        noise = np.tile([0.0, 1.0, -1.0, 0.5], 14)
        df = processed_frame(timestamps, "TEST_FLAT", 40 + noise, 10.0, 100.0)
        row = analyze(df, AnalysisConfig()).summary.iloc[0]
        self.assertEqual(row["trend_direction"], "ESTABLE")
        self.assertTrue(pd.isna(row["days_to_critical"]))

    def test_spearman_is_computed_without_scipy_dependency(self):
        a = pd.Series([1, 2, 2, 4, 5] * 5, dtype=float)
        b = pd.Series([10, 20, 20, 40, 50] * 5, dtype=float)
        value = _safe_spearman(a, b)
        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, 1.0, places=12)

    def test_quality_load_association_detected(self):
        timestamps = pd.date_range("2026-07-01", periods=300, freq="15min")
        utilization = np.linspace(0.2, 0.95, len(timestamps))
        rx = utilization * 100
        latency = 10 + utilization * 100
        loss = utilization * 4
        df = processed_frame(timestamps, "TEST_QUALITY", rx, 10.0, 100.0, latency, loss)
        row = analyze(df, AnalysisConfig()).summary.iloc[0]
        self.assertEqual(row["quality_load_association"], "FUERTE")
        self.assertGreater(row["util_latency_spearman"], 0.9)

    def test_technical_and_commercial_capacity_are_distinct(self):
        timestamps = pd.date_range("2026-07-01", periods=96, freq="15min")
        df = processed_frame(timestamps, "TEST_CAP", 143.0, 40.0, 150.0)
        row = analyze(df, AnalysisConfig()).summary.iloc[0]
        self.assertAlmostEqual(row["technical_required_capacity_mbps"], 143 / 0.70, places=5)
        self.assertGreaterEqual(row["recommended_capacity_mbps"], row["technical_required_capacity_mbps"])
        self.assertEqual(row["recommended_capacity_mbps"], 250.0)


if __name__ == "__main__":
    unittest.main()
