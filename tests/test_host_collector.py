from __future__ import annotations

from collections import namedtuple
import csv
from pathlib import Path
import tempfile
import unittest

from nettwin.cli import build_parser
from nettwin.host_collector import HostCollector, _load_average, collect_host


Memory = namedtuple("Memory", "percent")


class FakeProvider:
    def __init__(self, *, cpu=12.5, memory=34.5, loads=(0.2, 0.3, 0.4), boot=1000.0, fail_on_cpu=False):
        self.cpu = cpu
        self.memory = memory
        self.loads = loads
        self.boot = boot
        self.fail_on_cpu = fail_on_cpu
        self.calls = 0

    def cpu_percent(self, interval=None):
        self.calls += 1
        if self.fail_on_cpu and self.calls == 1:
            raise RuntimeError("fallo sintético CPU")
        return self.cpu

    def virtual_memory(self):
        return Memory(self.memory)

    def getloadavg(self):
        return self.loads

    def boot_time(self):
        return self.boot


class NoLoadProvider(FakeProvider):
    def getloadavg(self):
        raise NotImplementedError


class HostCollectorTests(unittest.TestCase):
    def test_sample_reports_cpu_memory_load_and_uptime(self):
        provider = FakeProvider()
        collector = HostCollector(
            provider=provider,
            clock=lambda: "2026-08-09T15:00:00+00:00",
            wall_clock=lambda: 1600.0,
        )
        sample = collector.sample()
        self.assertEqual(sample.timestamp, "2026-08-09T15:00:00+00:00")
        self.assertEqual(sample.cpu_percent, 12.5)
        self.assertEqual(sample.memory_percent, 34.5)
        self.assertEqual(sample.load_1m, 0.2)
        self.assertEqual(sample.load_5m, 0.3)
        self.assertEqual(sample.load_15m, 0.4)
        self.assertEqual(sample.uptime_seconds, 600.0)
        self.assertEqual(sample.sample_status, "ok")

    def test_load_average_is_optional_when_platform_does_not_support_it(self):
        provider = NoLoadProvider()
        sample = HostCollector(provider=provider, wall_clock=lambda: 1600.0).sample()
        self.assertIsNone(sample.load_1m)
        self.assertIsNone(sample.load_5m)
        self.assertIsNone(sample.load_15m)
        self.assertEqual(sample.sample_status, "ok")

    def test_failure_becomes_error_sample_instead_of_crash(self):
        provider = FakeProvider(fail_on_cpu=True)
        sample = HostCollector(provider=provider).sample()
        self.assertEqual(sample.sample_status, "error")
        self.assertIn("fallo sintético CPU", sample.error)
        self.assertIsNone(sample.cpu_percent)

    def test_collection_continues_after_transient_failure(self):
        provider = FakeProvider(fail_on_cpu=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host_samples.csv"
            collect_host(
                path,
                interval_seconds=0.01,
                samples=3,
                provider=provider,
                sleep_fn=lambda _: None,
                wall_clock=lambda: 1600.0,
            )
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["sample_status"], "error")
        self.assertEqual(rows[1]["sample_status"], "ok")
        self.assertEqual(rows[2]["sample_status"], "ok")

    def test_collect_host_writes_requested_number_of_samples(self):
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host_samples.csv"
            collect_host(
                path,
                interval_seconds=0.01,
                samples=4,
                provider=provider,
                sleep_fn=lambda _: None,
                wall_clock=lambda: 1600.0,
            )
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["sample_status"] == "ok" for row in rows))

    def test_invalid_limits_fail_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host.csv"
            with self.assertRaises(ValueError):
                collect_host(path, interval_seconds=0, samples=1)
            with self.assertRaises(ValueError):
                collect_host(path, interval_seconds=1, samples=0)

    def test_cli_exposes_collect_host(self):
        parser = build_parser()
        action = next(action for action in parser._actions if getattr(action, "choices", None))
        self.assertIn("collect-host", action.choices)


if __name__ == "__main__":
    unittest.main()
