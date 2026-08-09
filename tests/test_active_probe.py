from __future__ import annotations

import csv
from pathlib import Path
import subprocess
import tempfile
import unittest

from nettwin.active_probe import (
    ActiveProbeEngine,
    ProbeTarget,
    _build_ping_command,
    _parse_rtts,
    collect_probes,
)
from nettwin.cli import build_parser


class ActiveProbeTests(unittest.TestCase):
    def test_windows_command_uses_small_explicit_payload(self):
        command = _build_ping_command(
            "192.0.2.1",
            count=2,
            timeout_ms=900,
            payload_bytes=32,
            system_name="Windows",
        )
        self.assertEqual(
            command,
            ["ping", "-n", "2", "-w", "900", "-l", "32", "192.0.2.1"],
        )

    def test_linux_command_uses_small_explicit_payload(self):
        command = _build_ping_command(
            "192.0.2.1",
            count=1,
            timeout_ms=1200,
            payload_bytes=32,
            system_name="Linux",
        )
        self.assertEqual(
            command,
            ["ping", "-c", "1", "-W", "2", "-s", "32", "192.0.2.1"],
        )

    def test_parse_rtts_supports_english_and_spanish(self):
        text = "Reply: time=12ms\nRespuesta: tiempo=18ms\nRespuesta: tiempo<1ms"
        self.assertEqual(_parse_rtts(text), [12.0, 18.0, 0.5])

    def test_probe_reports_loss_reachability_and_temporal_variation(self):
        outputs = iter([
            "Reply from 192.0.2.1: time=10ms TTL=64",
            "Reply from 192.0.2.1: time=16ms TTL=64",
        ])

        def runner(command, timeout_seconds):
            return subprocess.CompletedProcess(command, 0, next(outputs), "")

        engine = ActiveProbeEngine(
            count_per_target=1,
            runner=runner,
            system_name="Windows",
            clock=lambda: "2026-08-09T16:00:00+00:00",
        )
        target = ProbeTarget("internal", "192.0.2.1")
        first = engine.probe(target)
        second = engine.probe(target)

        self.assertTrue(first.reachability)
        self.assertEqual(first.packet_loss_pct, 0.0)
        self.assertEqual(first.rtt_ms, 10.0)
        self.assertIsNone(first.delay_variation_ms)
        self.assertEqual(second.rtt_ms, 16.0)
        self.assertEqual(second.delay_variation_ms, 6.0)

    def test_unreachable_target_is_measurement_not_engine_crash(self):
        def runner(command, timeout_seconds):
            return subprocess.CompletedProcess(command, 1, "Request timed out.", "")

        engine = ActiveProbeEngine(count_per_target=2, runner=runner, system_name="Windows")
        sample = engine.probe(ProbeTarget("unreachable", "192.0.2.99"))
        self.assertFalse(sample.reachability)
        self.assertEqual(sample.packets_sent, 2)
        self.assertEqual(sample.packets_received, 0)
        self.assertEqual(sample.packet_loss_pct, 100.0)
        self.assertEqual(sample.sample_status, "ok")
        self.assertEqual(sample.error, "")

    def test_one_runner_failure_does_not_stop_other_targets(self):
        def runner(command, timeout_seconds):
            address = command[-1]
            if address == "192.0.2.10":
                raise RuntimeError("fallo sintético de ping")
            return subprocess.CompletedProcess(command, 0, "Reply: time=7ms", "")

        engine = ActiveProbeEngine(runner=runner, system_name="Windows")
        samples = engine.probe_all([
            ProbeTarget("bad", "192.0.2.10"),
            ProbeTarget("good", "192.0.2.20"),
        ])
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0].sample_status, "error")
        self.assertFalse(samples[0].reachability)
        self.assertEqual(samples[1].sample_status, "ok")
        self.assertTrue(samples[1].reachability)
        self.assertEqual(samples[1].rtt_ms, 7.0)

    def test_collect_probes_writes_exact_cycles_without_hidden_probe(self):
        calls = []

        def runner(command, timeout_seconds):
            calls.append(command[-1])
            return subprocess.CompletedProcess(command, 0, "Reply: time=5ms", "")

        targets = [
            ProbeTarget("a", "192.0.2.1"),
            ProbeTarget("b", "192.0.2.2"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe_samples.csv"
            collect_probes(
                targets,
                path,
                interval_seconds=0.01,
                cycles=3,
                runner=runner,
                sleep_fn=lambda _: None,
                system_name="Windows",
            )
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(calls), 6)
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["target"] for row in rows}, {"a", "b"})
        self.assertTrue(all(row["estimated_outbound_payload_bytes"] == "32" for row in rows))

    def test_cli_exposes_collect_probes_and_multiple_targets(self):
        parser = build_parser()
        args = parser.parse_args([
            "collect-probes",
            "--target", "internal=192.0.2.1",
            "--target", "external=198.51.100.1",
            "--cycles", "2",
        ])
        self.assertEqual(args.command, "collect-probes")
        self.assertEqual(len(args.target), 2)
        self.assertEqual(args.target[0].name, "internal")
        self.assertEqual(args.target[1].address, "198.51.100.1")
        self.assertEqual(args.count_per_target, 1)
        self.assertEqual(args.payload_bytes, 32)


if __name__ == "__main__":
    unittest.main()
