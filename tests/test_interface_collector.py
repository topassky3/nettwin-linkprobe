from __future__ import annotations

from collections import namedtuple
import csv
from pathlib import Path
import tempfile
import unittest

from nettwin.cli import build_parser
from nettwin.interface_collector import InterfaceCollector, _counter_delta, collect_interface, list_interfaces


Counters = namedtuple(
    "Counters",
    "bytes_sent bytes_recv packets_sent packets_recv errin errout dropin dropout",
)
Stats = namedtuple("Stats", "isup duplex speed mtu flags")


class FakeProvider:
    def __init__(self, counter_sequence, *, fail_on_call=None):
        self.counter_sequence = list(counter_sequence)
        self.index = 0
        self.fail_on_call = fail_on_call
        self.calls = 0

    def net_io_counters(self, pernic=True):
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("fallo sintético")
        idx = min(self.index, len(self.counter_sequence) - 1)
        value = self.counter_sequence[idx]
        self.index += 1
        return {"eth0": value}

    def net_if_stats(self):
        return {"eth0": Stats(True, 0, 1000, 1500, "")}


class InterfaceCollectorTests(unittest.TestCase):
    def test_counter_delta_never_returns_negative_on_reset(self):
        delta, event = _counter_delta(500, 20)
        self.assertIsNone(delta)
        self.assertEqual(event, "reset")

    def test_counter_overflow_can_be_identified_when_max_is_known(self):
        delta, event = _counter_delta(250, 5, max_value=255)
        self.assertEqual(delta, 11)
        self.assertEqual(event, "overflow")

    def test_collector_reports_raw_values_and_deltas(self):
        provider = FakeProvider([
            Counters(100, 200, 10, 20, 0, 0, 0, 0),
            Counters(150, 280, 14, 27, 1, 0, 0, 2),
        ])
        collector = InterfaceCollector("eth0", provider=provider, clock=lambda: "2026-08-09T15:00:00+00:00")
        first = collector.sample()
        second = collector.sample()
        self.assertEqual(first.counter_event, "initial")
        self.assertEqual(second.rx_bytes_delta, 80)
        self.assertEqual(second.tx_bytes_delta, 50)
        self.assertEqual(second.rx_packets_delta, 7)
        self.assertEqual(second.tx_packets_delta, 4)
        self.assertEqual(second.tx_drops_delta, 2)
        self.assertEqual(second.interface_state, "UP")
        self.assertEqual(second.reported_link_speed_mbps, 1000)
        self.assertEqual(second.mtu, 1500)

    def test_counter_reset_is_recorded_without_negative_delta(self):
        provider = FakeProvider([
            Counters(500, 900, 50, 90, 2, 3, 1, 1),
            Counters(10, 20, 2, 3, 0, 0, 0, 0),
        ])
        collector = InterfaceCollector("eth0", provider=provider)
        collector.sample()
        second = collector.sample()
        self.assertIn("reset", second.counter_event)
        self.assertIsNone(second.rx_bytes_delta)
        self.assertIsNone(second.tx_bytes_delta)
        self.assertIsNone(second.rx_errors_delta)

    def test_read_failure_becomes_error_sample_instead_of_crash(self):
        provider = FakeProvider(
            [Counters(100, 200, 10, 20, 0, 0, 0, 0)],
            fail_on_call=1,
        )
        collector = InterfaceCollector("eth0", provider=provider)
        sample = collector.sample()
        self.assertEqual(sample.sample_status, "error")
        self.assertEqual(sample.interface_state, "UNKNOWN")
        self.assertIn("fallo sintético", sample.error)

    def test_collect_interface_writes_requested_number_of_samples(self):
        provider = FakeProvider([
            Counters(100, 200, 10, 20, 0, 0, 0, 0),
            Counters(110, 220, 11, 22, 0, 0, 0, 0),
            Counters(120, 240, 12, 24, 0, 0, 0, 0),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "interface_samples.csv"
            collect_interface("eth0", path, interval_seconds=0.01, samples=3, provider=provider, sleep_fn=lambda _: None)
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["interface"] == "eth0" for row in rows))

    def test_collection_continues_after_transient_failure(self):
        provider = FakeProvider([
            Counters(100, 200, 10, 20, 0, 0, 0, 0),
            Counters(120, 240, 12, 24, 0, 0, 0, 0),
        ], fail_on_call=2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "interface_samples.csv"
            collect_interface("eth0", path, interval_seconds=0.01, samples=3, provider=provider, sleep_fn=lambda _: None)
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["sample_status"], "error")
        self.assertEqual(rows[2]["sample_status"], "ok")

    def test_list_interfaces_exposes_state_speed_mtu_and_counter_presence(self):
        provider = FakeProvider([Counters(1, 2, 3, 4, 0, 0, 0, 0)])
        rows = list_interfaces(provider)
        self.assertEqual(rows[0]["interface"], "eth0")
        self.assertTrue(rows[0]["is_up"])
        self.assertEqual(rows[0]["speed_mbps"], 1000)
        self.assertEqual(rows[0]["mtu"], 1500)
        self.assertTrue(rows[0]["has_counters"])

    def test_cli_exposes_interface_commands(self):
        parser = build_parser()
        args = parser.parse_args(["collect-interface", "--interface", "eth0", "--samples", "3"])
        self.assertEqual(args.command, "collect-interface")
        self.assertEqual(args.interface, "eth0")
        self.assertEqual(args.samples, 3)
        interfaces_args = parser.parse_args(["interfaces"])
        self.assertEqual(interfaces_args.command, "interfaces")


if __name__ == "__main__":
    unittest.main()
