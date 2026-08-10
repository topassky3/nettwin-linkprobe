from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
NETTWIN = REPO_ROOT / "nettwin.py"


class Phase14CliTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(NETTWIN), *args],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            env=env,
        )

    def test_public_help_exposes_dry_run_command(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertIn("pipeline local completo", result.stdout)

    def test_dry_run_help_exposes_safe_local_options(self):
        result = self._run("dry-run", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in (
            "--output", "--interface", "--duration-seconds", "--interval-seconds",
            "--run-id", "--company", "--link", "--capacity-mbps",
            "--redact-local-addresses",
        ):
            self.assertIn(option, result.stdout)
        self.assertIn("127.0.0.1", result.stdout)


if __name__ == "__main__":
    unittest.main()
