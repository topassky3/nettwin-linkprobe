from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import socket
import tempfile
import unittest

from nettwin.preflight_cli import build_parser
from nettwin.preflight_engine import (
    CommandResult,
    _parse_darwin_gateway,
    _parse_linux_gateway,
    _parse_windows_gateway,
    load_experiment_config,
)
from nettwin.preflight_runtime import run_preflight


DiskUsage = namedtuple("DiskUsage", "total used free")


class FakeProvider:
    def __init__(self, *, exists=True, up=True, counters=True, speed=1000, addresses=True):
        self.exists = exists
        self.up = up
        self.counters = counters
        self.speed = speed
        self.with_addresses = addresses

    def net_if_stats(self):
        if not self.exists:
            return {}
        return {"eth0": SimpleNamespace(isup=self.up, speed=self.speed, mtu=1500)}

    def net_io_counters(self, pernic=True):
        if not self.exists or not self.counters:
            return {}
        return {"eth0": SimpleNamespace()}

    def net_if_addrs(self):
        if not self.exists or not self.with_addresses:
            return {}
        return {
            "eth0": [
                SimpleNamespace(family=socket.AF_INET, address="10.0.0.10", netmask="255.255.255.0"),
                SimpleNamespace(family=socket.AF_INET6, address="2001:db8::10", netmask="ffff:ffff:ffff:ffff::"),
            ]
        }


def good_which(name: str):
    return {"ping": "/usr/bin/ping", "ip": "/usr/sbin/ip", "route": "/sbin/route"}.get(name)


def linux_runner(command):
    return CommandResult(0, "default via 10.0.0.1 dev eth0 proto dhcp metric 100\n", "")


def fixed_clock():
    return {
        "local_time": "2026-08-09T13:00:00-05:00",
        "utc_time": "2026-08-09T18:00:00+00:00",
        "timezone_name": "-05",
        "utc_offset_seconds": -18000,
    }


def ample_disk(_path):
    gib = 1024 ** 3
    return DiskUsage(total=100 * gib, used=10 * gib, free=90 * gib)


def low_disk(_path):
    mib = 1024 ** 2
    return DiskUsage(total=100 * mib, used=99 * mib, free=1 * mib)


class PreflightEngineTests(unittest.TestCase):
    def _config(self, root: Path, *, targets=1, interface="eth0", minimum_free_disk_mb=50):
        target_rows = []
        addresses = ["192.0.2.1", "198.51.100.1", "203.0.113.1", "example.net"]
        for index in range(targets):
            target_rows.append({
                "name": f"target-{index+1}",
                "address": addresses[index % len(addresses)],
                "role": "debug",
            })
        payload = {
            "run": {"run_id": "TEST-RUN-001", "duration_seconds": 14400},
            "interface": {"name": interface, "interval_seconds": 5, "capacity_mbps": 100},
            "host": {"interval_seconds": 5},
            "probes": {"interval_seconds": 5, "timeout_ms": 1000, "payload_bytes": 32, "count_per_target": 1},
            "targets": target_rows,
            "output": {"directory": "./run", "minimum_free_disk_mb": minimum_free_disk_mb},
        }
        path = root / "preflight_config.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _run(self, config_path: Path, **kwargs):
        defaults = dict(
            provider=FakeProvider(),
            which_fn=good_which,
            runner=linux_runner,
            disk_usage_fn=ample_disk,
            clock_fn=fixed_clock,
            hostname_fn=lambda: "server01",
            system_name="Linux",
            sensor_version="0.3-dev",
        )
        defaults.update(kwargs)
        return run_preflight(config_path, **defaults)

    def test_config_hash_and_relative_output_are_recorded_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config(root)
            config = load_experiment_config(config_path)
            expected = hashlib.sha256(config_path.read_bytes()).hexdigest()
            self.assertEqual(config.config_sha256, expected)
            self.assertEqual(config.output_directory, (root / "run").resolve())

    def test_duplicate_target_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config(root, targets=2)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["targets"][1]["name"] = payload["targets"][0]["name"]
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicado"):
                load_experiment_config(config_path)

    def test_target_url_or_port_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["targets"][0]["address"] = "https://example.net/path"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sin esquema"):
                load_experiment_config(config_path)

    def test_non_json_configuration_is_rejected_without_yaml_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("run: {}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON"):
                load_experiment_config(path)

    def test_linux_gateway_prefers_selected_interface(self):
        output = "default via 192.0.2.1 dev eth1 metric 10\ndefault via 10.0.0.1 dev eth0 metric 100\n"
        parsed = _parse_linux_gateway(output, "eth0")
        self.assertEqual(parsed["gateway"], "10.0.0.1")
        self.assertEqual(parsed["interface"], "eth0")

    def test_windows_gateway_uses_selected_interface_ip_even_if_metric_is_higher(self):
        output = (
            "          0.0.0.0          0.0.0.0       10.1.0.1       10.1.0.10      5\n"
            "          0.0.0.0          0.0.0.0       10.0.0.1       10.0.0.10     25\n"
        )
        parsed = _parse_windows_gateway(output, {"10.0.0.10"})
        self.assertEqual(parsed["gateway"], "10.0.0.1")
        self.assertEqual(parsed["interface_ip"], "10.0.0.10")

    def test_darwin_gateway_parser(self):
        parsed = _parse_darwin_gateway(
            "   route to: default\ndestination: default\n    gateway: 10.0.0.1\n  interface: en0\n"
        )
        self.assertEqual(parsed["gateway"], "10.0.0.1")
        self.assertEqual(parsed["interface"], "en0")

    def test_healthy_preflight_is_ready_and_writes_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run(self._config(root))
            payload = result.payload
            self.assertTrue(payload["ready_for_run"])
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["schema_version"], "preflight-v1")
            self.assertEqual(payload["run_id"], "TEST-RUN-001")
            self.assertEqual(payload["interface"]["selected"], "eth0")
            self.assertEqual(payload["interface"]["mtu"], 1500)
            self.assertEqual(payload["interface"]["reported_link_speed_mbps"], 1000)
            self.assertEqual(payload["gateway"]["gateway"], "10.0.0.1")
            self.assertTrue(payload["tools"]["ping"]["available"])
            self.assertTrue(result.output_path.exists())

    def test_hostname_is_anonymized_by_default_with_run_scoped_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp)))
            system = result.payload["system"]
            expected = hashlib.sha256(b"TEST-RUN-001\x00server01").hexdigest()
            self.assertIsNone(system["hostname"])
            self.assertEqual(system["hostname_sha256"], expected)
            self.assertEqual(system["hostname_hash_scope"], "run_id_salted")

    def test_hostname_can_be_included_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp)), include_hostname=True)
            self.assertEqual(result.payload["system"]["hostname"], "server01")

    def test_redacted_local_addresses_keep_gateway_selection_and_remove_ip_from_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config(root)

            def which(name):
                return {"ping": "C:\\Windows\\System32\\PING.EXE", "route": "C:\\Windows\\System32\\ROUTE.EXE"}.get(name)

            def windows_runner(command):
                return CommandResult(
                    0,
                    "          0.0.0.0          0.0.0.0       10.1.0.1       10.1.0.10      5\n"
                    "          0.0.0.0          0.0.0.0       10.0.0.1       10.0.0.10     25\n",
                    "",
                )

            result = run_preflight(
                config_path,
                provider=FakeProvider(),
                which_fn=which,
                runner=windows_runner,
                disk_usage_fn=ample_disk,
                clock_fn=fixed_clock,
                hostname_fn=lambda: "server01",
                system_name="Windows",
                redact_local_addresses=True,
            )
            self.assertEqual(result.payload["gateway"]["gateway"], "10.0.0.1")
            for row in result.payload["interface"]["addresses"]:
                self.assertIsNone(row["address"])
                self.assertIsNotNone(row["address_sha256"])

    def test_missing_interface_is_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp)), provider=FakeProvider(exists=False))
            self.assertFalse(result.payload["ready_for_run"])
            self.assertEqual(result.payload["status"], "FAIL")
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["interface.exists"], "FAIL")

    def test_down_interface_is_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp)), provider=FakeProvider(up=False))
            self.assertFalse(result.payload["ready_for_run"])
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["interface.up"], "FAIL")

    def test_missing_interface_counters_is_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp)), provider=FakeProvider(counters=False))
            self.assertFalse(result.payload["ready_for_run"])
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["interface.counters"], "FAIL")

    def test_missing_ping_is_hard_fail_without_running_a_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = []

            def which(name):
                return "/usr/sbin/ip" if name == "ip" else None

            def runner(command):
                commands.append(list(command))
                return linux_runner(command)

            result = self._run(self._config(Path(tmp)), which_fn=which, runner=runner)
            self.assertFalse(result.payload["ready_for_run"])
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["tool.ping"], "FAIL")
            self.assertEqual(commands, [["ip", "-4", "route", "show", "default"]])

    def test_insufficient_disk_is_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp), minimum_free_disk_mb=50), disk_usage_fn=low_disk)
            self.assertFalse(result.payload["ready_for_run"])
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["disk.free_space"], "FAIL")

    def test_gateway_unavailable_is_warning_not_invented_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            def which(name):
                return "/usr/bin/ping" if name == "ping" else None

            result = self._run(self._config(Path(tmp)), which_fn=which)
            self.assertTrue(result.payload["ready_for_run"])
            self.assertEqual(result.payload["status"], "WARN")
            self.assertIsNone(result.payload["gateway"]["gateway"])
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["gateway.observed"], "WARN")

    def test_four_hour_three_target_estimate_matches_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp), targets=3))
            expected = result.payload["experiment"]["expected_samples"]
            traffic = result.payload["experiment"]["probe_traffic_estimate"]
            self.assertEqual(expected["interface_rows"], 2880)
            self.assertEqual(expected["host_rows"], 2880)
            self.assertEqual(expected["probe_cycles_per_target"], 2880)
            self.assertEqual(expected["probe_rows_total"], 8640)
            self.assertEqual(expected["total_rows"], 14400)
            self.assertEqual(traffic["estimated_outbound_payload_bytes"], 276480)

    def test_preflight_never_probes_targets_or_resolves_dns(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = []

            def runner(command):
                commands.append(list(command))
                return linux_runner(command)

            result = self._run(self._config(Path(tmp)), runner=runner)
            safety = result.payload["network_safety"]
            self.assertFalse(safety["active_probe_performed"])
            self.assertFalse(safety["dns_resolution_performed"])
            self.assertFalse(safety["routes_modified"])
            self.assertFalse(safety["firewall_modified"])
            self.assertFalse(safety["interfaces_modified"])
            self.assertFalse(safety["services_modified"])
            self.assertFalse(safety["network_configuration_modified"])
            self.assertEqual(commands, [["ip", "-4", "route", "show", "default"]])
            self.assertNotIn("192.0.2.1", " ".join(commands[0]))

    def test_preflight_json_contains_exact_config_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config(root)
            expected = hashlib.sha256(config_path.read_bytes()).hexdigest()
            result = self._run(config_path)
            payload = json.loads(result.output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["configuration"]["sha256"], expected)
            self.assertEqual(payload["scope"], "observational_only")

    def test_more_than_three_targets_is_warning_but_not_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(self._config(Path(tmp), targets=4))
            self.assertTrue(result.payload["ready_for_run"])
            self.assertEqual(result.payload["status"], "WARN")
            statuses = {c["id"]: c["status"] for c in result.payload["checks"]}
            self.assertEqual(statuses["targets.count"], "WARN")

    def test_cli_exposes_preflight_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "--config", "config.json", "--output", "preflight.json",
            "--include-hostname", "--redact-local-addresses",
            "--sensor-version", "0.3-dev",
        ])
        self.assertEqual(args.config, "config.json")
        self.assertEqual(args.output, "preflight.json")
        self.assertTrue(args.include_hostname)
        self.assertTrue(args.redact_local_addresses)
        self.assertEqual(args.sensor_version, "0.3-dev")


if __name__ == "__main__":
    unittest.main()
