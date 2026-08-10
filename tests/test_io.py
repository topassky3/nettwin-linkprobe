from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from nettwin.io import DataValidationError, load_and_validate_csv


class IOTests(unittest.TestCase):
    def test_missing_required_column_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            pd.DataFrame({"timestamp": ["2026-01-01"], "link_id": ["A"]}).to_csv(path, index=False)
            with self.assertRaisesRegex(DataValidationError, "Faltan columnas obligatorias"):
                load_and_validate_csv(path)

    def test_common_spanish_aliases_are_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.csv"
            pd.DataFrame(
                {
                    "Fecha": ["2026-01-01 00:00:00"],
                    "Enlace": ["A"],
                    "Entrada Mbps": [20],
                    "Salida Mbps": [10],
                    "Capacidad": [100],
                }
            ).to_csv(path, index=False)
            result = load_and_validate_csv(path)
            self.assertEqual(result.accepted_rows, 1)
            self.assertIn("Fecha", result.mapped_columns)
            self.assertIn("rx_utilization", result.data.columns)

    def test_manual_mapping_supports_vendor_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vendor.csv"
            mapping = Path(tmp) / "mapping.json"
            pd.DataFrame(
                {
                    "clock": ["2026-01-01 00:00:00"],
                    "iface": ["A"],
                    "bits_in": [20],
                    "bits_out": [10],
                    "limit": [100],
                }
            ).to_csv(path, index=False)
            mapping.write_text(
                json.dumps({
                    "clock": "timestamp", "iface": "link_id", "bits_in": "rx_mbps",
                    "bits_out": "tx_mbps", "limit": "capacity_mbps",
                }),
                encoding="utf-8",
            )
            result = load_and_validate_csv(path, mapping)
            self.assertEqual(result.accepted_rows, 1)

    def test_invalid_and_duplicate_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.csv"
            pd.DataFrame(
                {
                    "timestamp": ["2026-01-01 00:00", "2026-01-01 00:00", "bad"],
                    "link_id": ["A", "A", "B"],
                    "rx_mbps": [20, 21, 10],
                    "tx_mbps": [10, 11, 5],
                    "capacity_mbps": [100, 100, 0],
                }
            ).to_csv(path, index=False)
            result = load_and_validate_csv(path)
            self.assertEqual(result.accepted_rows, 1)
            self.assertEqual(result.rejected_rows, 2)
            reasons = " ".join(result.rejected["rejection_reason"].tolist())
            self.assertIn("duplicado", reasons)

    def test_over_capacity_is_preserved_and_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "over.csv"
            pd.DataFrame(
                {
                    "timestamp": ["2026-01-01 00:00"],
                    "link_id": ["A"],
                    "rx_mbps": [110],
                    "tx_mbps": [10],
                    "capacity_mbps": [100],
                }
            ).to_csv(path, index=False)
            result = load_and_validate_csv(path)
            self.assertTrue(bool(result.data.iloc[0]["over_capacity"]))
            self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
