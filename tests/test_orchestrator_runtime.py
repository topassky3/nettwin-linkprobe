from __future__ import annotations

import hashlib
import json
from pathlib import Path
import signal
import tempfile
import threading
import unittest

from nettwin.orchestrator_engine import _install_signal_handlers, _restore_signal_handlers
from nettwin.preflight_runtime import load_experiment_config_compatible


class OrchestratorRuntimeTests(unittest.TestCase):
    def test_utf8_bom_loader_preserves_original_identity_and_relative_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "run": {"run_id": "BOM-ORCH-001", "duration_seconds": 12},
                "interface": {"name": "eth0", "interval_seconds": 2, "capacity_mbps": None},
                "host": {"interval_seconds": 2},
                "probes": {"interval_seconds": 2, "timeout_ms": 1000, "payload_bytes": 32, "count_per_target": 1},
                "targets": [{"name": "local", "address": "127.0.0.1", "role": "test"}],
                "output": {"directory": "./run", "minimum_free_disk_mb": 1},
            }
            path = root / "config_bom.json"
            path.write_text(json.dumps(payload), encoding="utf-8-sig")
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()

            config = load_experiment_config_compatible(path)

            self.assertEqual(config.config_path, path.resolve())
            self.assertEqual(config.config_sha256, expected_hash)
            self.assertEqual(config.output_directory, (root / "run").resolve())
            self.assertEqual(config.run_id, "BOM-ORCH-001")

    def test_sigint_handler_sets_shared_stop_event_and_reason(self):
        stop_event = threading.Event()
        reason = {"value": "duration_elapsed"}
        previous, installed = _install_signal_handlers(stop_event, reason)
        self.assertTrue(installed)
        try:
            signal.raise_signal(signal.SIGINT)
            self.assertTrue(stop_event.wait(1.0))
            self.assertEqual(reason["value"], "signal:SIGINT")
        finally:
            _restore_signal_handlers(previous)


if __name__ == "__main__":
    unittest.main()
