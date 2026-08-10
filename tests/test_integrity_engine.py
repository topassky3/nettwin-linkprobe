from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nettwin.integrity_cli import build_parser
from nettwin.integrity_engine import (
    CHECKSUM_FILENAME,
    IntegrityConfig,
    _compare_semantic,
    check_reproducibility,
    finalize_integrity,
    verify_integrity,
)
from scripts.generate_integrity_debug_run import generate


class IntegrityEngineTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        run_dir = root / "run"
        generate(run_dir)
        return run_dir

    def _finalize(self, run_dir: Path):
        return finalize_integrity(
            run_dir,
            config=IntegrityConfig(
                run_id="HACHENET-20260809-LINK01-001",
                requested_duration_seconds=115,
                config_path=run_dir / "run_config.json",
            ),
        )

    def test_finalize_writes_required_metadata_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            result = self._finalize(run_dir)
            payload = json.loads(result.metadata_path.read_text(encoding="utf-8"))
            self.assertTrue(result.checksums_path.exists())
        self.assertEqual(payload["run_id"], "HACHENET-20260809-LINK01-001")
        self.assertIn("sensor_version", payload["software"])
        self.assertIn("analyzer_version", payload["software"])
        self.assertEqual(payload["interface"], "eth0")
        self.assertEqual(payload["targets"], ["external"])
        self.assertEqual(payload["observed_window"]["duration_seconds"], 115.0)
        self.assertEqual(payload["observed_window"]["requested_duration_seconds"], 115)
        self.assertEqual(payload["sample_summary"]["total_rows"], 72)
        self.assertEqual(payload["sample_summary"]["failed_rows_known_total"], 0)

    def test_checksums_are_sorted_relative_and_include_run_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            result = self._finalize(run_dir)
            lines = result.checksums_path.read_text(encoding="utf-8").splitlines()
        paths = [line.split("  ", 1)[1] for line in lines]
        self.assertEqual(paths, sorted(paths))
        self.assertIn("run_metadata.json", paths)
        self.assertIn("interface_samples.csv", paths)
        self.assertTrue(all(not Path(path).is_absolute() for path in paths))

    def test_verify_accepts_pristine_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            self._finalize(run_dir)
            result = verify_integrity(run_dir)
        self.assertTrue(result.valid)
        self.assertEqual(result.missing_files, ())
        self.assertEqual(result.mismatched_files, ())

    def test_verify_detects_modified_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            self._finalize(run_dir)
            with (run_dir / "interface_samples.csv").open("a", encoding="utf-8") as handle:
                handle.write("tamper\n")
            result = verify_integrity(run_dir)
        self.assertFalse(result.valid)
        self.assertIn("interface_samples.csv", result.mismatched_files)

    def test_verify_detects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            self._finalize(run_dir)
            (run_dir / "host_samples.csv").unlink()
            result = verify_integrity(run_dir)
        self.assertFalse(result.valid)
        self.assertIn("host_samples.csv", result.missing_files)

    def test_untracked_file_is_reported_and_strict_mode_rejects_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            self._finalize(run_dir)
            (run_dir / "after-finalize.txt").write_text("nuevo", encoding="utf-8")
            normal = verify_integrity(run_dir)
            strict = verify_integrity(run_dir, strict_untracked=True)
        self.assertTrue(normal.valid)
        self.assertIn("after-finalize.txt", normal.untracked_files)
        self.assertFalse(strict.valid)

    def test_unsafe_manifest_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            digest = "0" * 64
            (run_dir / CHECKSUM_FILENAME).write_text(
                f"{digest}  ../outside.txt\n",
                encoding="utf-8",
            )
            result = verify_integrity(run_dir)
        self.assertFalse(result.valid)
        self.assertEqual(result.unsafe_paths, ("../outside.txt",))

    def test_malformed_and_duplicate_checksum_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            digest = hashlib.sha256((run_dir / "run_config.json").read_bytes()).hexdigest()
            (run_dir / CHECKSUM_FILENAME).write_text(
                "not-a-checksum\n"
                f"{digest}  run_config.json\n"
                f"{digest}  run_config.json\n",
                encoding="utf-8",
            )
            result = verify_integrity(run_dir)
        self.assertFalse(result.valid)
        self.assertEqual(len(result.malformed_lines), 2)

    def test_failed_samples_are_counted_when_status_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            path = run_dir / "host_samples.csv"
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(",ok,\n", ",error,falla\n", 1), encoding="utf-8")
            result = self._finalize(run_dir)
        self.assertEqual(result.metadata["sample_summary"]["failed_rows_known_total"], 1)

    def test_configuration_hash_is_recorded_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            result = self._finalize(run_dir)
            expected = hashlib.sha256((run_dir / "run_config.json").read_bytes()).hexdigest()
        self.assertEqual(result.metadata["configuration"]["sha256"], expected)

    def test_refinalizing_unchanged_run_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            first = self._finalize(run_dir)
            metadata_first = first.metadata_path.read_bytes()
            checksum_first = first.checksums_path.read_bytes()
            second = self._finalize(run_dir)
            metadata_second = second.metadata_path.read_bytes()
            checksum_second = second.checksums_path.read_bytes()
        self.assertEqual(metadata_first, metadata_second)
        self.assertEqual(checksum_first, checksum_second)

    def test_reproducibility_replays_full_analytics_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._run(root)
            result = check_reproducibility(
                run_dir / "interface_samples.csv",
                run_dir / "probe_samples.csv",
                host_csv=run_dir / "host_samples.csv",
                capacity_mbps=100.0,
                output_dir=root / "repro",
            )
            payload = json.loads(result.report_path.read_text(encoding="utf-8"))
        self.assertTrue(result.reproducible)
        self.assertEqual(len(result.compared_artifacts), 6)
        self.assertEqual(result.differences, ())
        self.assertTrue(payload["reproducible"])
        self.assertEqual(payload["numeric_tolerance"]["absolute"], 1e-9)

    def test_semantic_numeric_comparison_respects_documented_tolerance(self):
        differences: list[dict] = []
        _compare_semantic(
            {"x": 1.0}, {"x": 1.0 + 5e-10},
            abs_tol=1e-9, rel_tol=1e-9, differences=differences,
        )
        self.assertEqual(differences, [])
        _compare_semantic(
            {"x": 1.0}, {"x": 1.001},
            abs_tol=1e-9, rel_tol=1e-9, differences=differences,
        )
        self.assertTrue(differences)

    def test_cli_exposes_all_phase10_commands(self):
        parser = build_parser()
        finalize = parser.parse_args(
            ["finalize-integrity", "--run-dir", "run", "--run-id", "RUN-1"]
        )
        verify = parser.parse_args(["verify-integrity", "--run-dir", "run"])
        repro = parser.parse_args(
            [
                "repro-check",
                "--interface-csv", "interface.csv",
                "--probe-csv", "probe.csv",
                "--output", "repro",
            ]
        )
        self.assertEqual(finalize.integrity_command, "finalize-integrity")
        self.assertEqual(verify.integrity_command, "verify-integrity")
        self.assertEqual(repro.integrity_command, "repro-check")


if __name__ == "__main__":
    unittest.main()
