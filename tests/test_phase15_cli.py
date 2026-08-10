from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
NETTWIN = REPO_ROOT / "nettwin.py"


class Phase15CliTests(unittest.TestCase):
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

    def test_public_help_exposes_phase15_commands_and_v030(self):
        help_result = self._run("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in ("pilot-config", "pilot-validate", "pilot-run"):
            self.assertIn(command, help_result.stdout)

        version_result = self._run("--version")
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        self.assertIn("0.3.0", version_result.stdout)

        run_help = self._run("pilot-run", "--help")
        self.assertEqual(run_help.returncode, 0, run_help.stderr)
        self.assertIn("--authorized", run_help.stdout)
        self.assertIn("HacheNet", run_help.stdout)

    def test_public_cli_generates_and_validates_loopback_local_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "pilot_local.json"
            generated = self._run(
                "pilot-config",
                "--output", str(config),
                "--local-acceptance",
                "--interface", "eth0",
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertTrue(config.is_file())
            self.assertIn("127.0.0.1", generated.stdout)

            validated = self._run("pilot-validate", "--config", str(config), "--json")
            self.assertEqual(validated.returncode, 0, validated.stderr)
            payload = json.loads(validated.stdout)
            self.assertTrue(payload["valid"])
            self.assertEqual(payload["mode"], "local_acceptance")
            self.assertEqual(payload["estimate"]["icmp_echo_requests"], 6)
            self.assertEqual(payload["estimate"]["expected_rows"]["total"], 18)


if __name__ == "__main__":
    unittest.main()
