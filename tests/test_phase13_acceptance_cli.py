from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "generate_report_debug_run.py"
NETTWIN = REPO_ROOT / "nettwin.py"


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


class Phase13AcceptanceCliTests(unittest.TestCase):
    def test_debug_generator_runs_exactly_like_documented_powershell_command(self):
        """Regresión: ejecutar scripts/*.py por ruta debe encontrar el paquete nettwin."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "acceptance"
            completed = _run_command(
                [sys.executable, str(GENERATOR), "--output", str(root)]
            )

            self.assertEqual(
                completed.returncode,
                0,
                msg=(
                    "El generador público falló como comando real.\n"
                    f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
                ),
            )
            self.assertNotIn("ModuleNotFoundError", completed.stderr)
            self.assertIn("Integridad estricta: VÁLIDA", completed.stdout)

            run = root / "run"
            for name in (
                "experiment_config.json",
                "preflight.json",
                "interface_samples.csv",
                "host_samples.csv",
                "probe_samples.csv",
                "orchestration.json",
                "run_metadata.json",
                "checksums.sha256",
            ):
                self.assertTrue((run / name).is_file(), msg=f"Falta {name}")

    def test_public_cli_end_to_end_generator_then_report(self):
        """Prueba de caja negra del flujo documentado que ejecuta el usuario."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "acceptance"
            generated = _run_command(
                [sys.executable, str(GENERATOR), "--output", str(root)]
            )
            self.assertEqual(
                generated.returncode,
                0,
                msg=f"Generador falló:\n{generated.stdout}\n{generated.stderr}",
            )

            run = root / "run"
            report = root / "report"
            completed = _run_command(
                [
                    sys.executable,
                    str(NETTWIN),
                    "report",
                    "--run",
                    str(run),
                    "--output",
                    str(report),
                    "--company",
                    "HacheNet",
                    "--link",
                    "LINK-01",
                ]
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=(
                    "El pipeline público generator -> report falló.\n"
                    f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
                ),
            )
            self.assertIn("Quality Gate: PASS", completed.stdout)
            self.assertIn("Pipeline analítico ejecutado: SÍ", completed.stdout)

            required = (
                "dataset_quality.json",
                "dataset_quality_summary.csv",
                "analysis.json",
                "report.json",
                "report.html",
            )
            for name in required:
                self.assertTrue((report / name).is_file(), msg=f"Falta {name}")

            payload = json.loads((report / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["integrity"]["valid"])
            self.assertEqual(payload["dataset_quality"]["status"], "PASS")
            self.assertTrue(payload["dataset_quality"]["safe_for_conclusions"])
            self.assertTrue(payload["analysis_executed"])
            self.assertGreaterEqual(len(payload["events"]), 1)
            self.assertGreaterEqual(len(payload["findings"]), 1)
            self.assertGreaterEqual(len(payload["fingerprints"]), 1)

            analysis = json.loads((report / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["run_id"], payload["run_id"])
            self.assertTrue(analysis["analysis_executed"])
            self.assertFalse(analysis["causal_inference_performed"])


if __name__ == "__main__":
    unittest.main()
