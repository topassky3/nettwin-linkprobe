from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import socket
import tempfile
import unittest

from nettwin.preflight_engine import CommandResult
from nettwin.preflight_runtime import run_preflight
from scripts.generate_preflight_debug_config import _pick_interface


DiskUsage = namedtuple("DiskUsage", "total used free")


class SingleInterfaceProvider:
    def net_if_stats(self):
        return {"eth0": SimpleNamespace(isup=True, speed=1000, mtu=1500)}

    def net_io_counters(self, pernic=True):
        return {"eth0": SimpleNamespace()}

    def net_if_addrs(self):
        return {
            "eth0": [
                SimpleNamespace(
                    family=socket.AF_INET,
                    address="10.0.0.10",
                    netmask="255.255.255.0",
                )
            ]
        }


class SelectorProvider:
    def net_if_stats(self):
        return {
            "Tailscale": SimpleNamespace(isup=True, speed=4294, mtu=65535),
            "Wi-Fi": SimpleNamespace(isup=True, speed=866, mtu=1500),
        }

    def net_io_counters(self, pernic=True):
        return {
            "Tailscale": SimpleNamespace(),
            "Wi-Fi": SimpleNamespace(),
        }

    def net_if_addrs(self):
        return {
            "Tailscale": [
                SimpleNamespace(
                    family=socket.AF_INET,
                    address="169.254.83.107",
                    netmask="255.255.0.0",
                )
            ],
            "Wi-Fi": [
                SimpleNamespace(
                    family=socket.AF_INET,
                    address="192.168.0.105",
                    netmask="255.255.255.0",
                )
            ],
        }


def ample_disk(_path):
    gib = 1024 ** 3
    return DiskUsage(total=100 * gib, used=10 * gib, free=90 * gib)


def fixed_clock():
    return {
        "local_time": "2026-08-09T14:30:00-05:00",
        "utc_time": "2026-08-09T19:30:00+00:00",
        "timezone_name": "-05",
        "utc_offset_seconds": -18000,
    }


def config_payload(interface: str = "eth0") -> dict:
    return {
        "run": {"run_id": "PHASE11-CLOSURE-001", "duration_seconds": 14400},
        "interface": {
            "name": interface,
            "interval_seconds": 5,
            "capacity_mbps": 100,
        },
        "host": {"interval_seconds": 5},
        "probes": {
            "interval_seconds": 5,
            "timeout_ms": 1000,
            "payload_bytes": 32,
            "count_per_target": 1,
        },
        "targets": [
            {
                "name": "documentation_reference",
                "address": "192.0.2.1",
                "role": "debug",
            }
        ],
        "output": {"directory": "./run", "minimum_free_disk_mb": 50},
    }


class Phase11ClosureTests(unittest.TestCase):
    def test_utf8_bom_configuration_is_accepted_and_original_hash_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "preflight_bom.json"
            config.write_text(
                json.dumps(config_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8-sig",
            )
            expected_hash = hashlib.sha256(config.read_bytes()).hexdigest()

            def which(name):
                return {"ping": "/usr/bin/ping", "ip": "/usr/sbin/ip"}.get(name)

            def runner(command):
                return CommandResult(
                    0,
                    "default via 10.0.0.1 dev eth0 metric 100\n",
                    "",
                )

            result = run_preflight(
                config,
                provider=SingleInterfaceProvider(),
                which_fn=which,
                runner=runner,
                disk_usage_fn=ample_disk,
                clock_fn=fixed_clock,
                hostname_fn=lambda: "server01",
                system_name="Linux",
                sensor_version="0.3-dev",
            )

            self.assertTrue(result.payload["ready_for_run"])
            self.assertEqual(result.payload["configuration"]["sha256"], expected_hash)
            self.assertEqual(result.payload["configuration"]["path"], str(config.resolve()))
            self.assertEqual(result.payload["configuration"]["encoding"], "utf-8-sig")
            self.assertFalse(any(root.glob(".nettwin_preflight_bom_*.json")))

    def test_windows_gateway_from_other_interface_is_not_attributed_to_selected_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "preflight.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")

            def which(name):
                return {
                    "ping": r"C:\Windows\System32\ping.exe",
                    "route": r"C:\Windows\System32\route.exe",
                }.get(name)

            def runner(command):
                return CommandResult(
                    0,
                    "          0.0.0.0          0.0.0.0       10.1.0.1       10.1.0.10      5\n",
                    "",
                )

            result = run_preflight(
                config,
                provider=SingleInterfaceProvider(),
                which_fn=which,
                runner=runner,
                disk_usage_fn=ample_disk,
                clock_fn=fixed_clock,
                hostname_fn=lambda: "server01",
                system_name="Windows",
                sensor_version="0.3-dev",
            )

            self.assertTrue(result.payload["ready_for_run"])
            self.assertEqual(result.payload["status"], "WARN")
            self.assertEqual(result.payload["gateway"]["status"], "unavailable")
            self.assertIsNone(result.payload["gateway"]["gateway"])
            self.assertFalse(result.payload["gateway"]["selected_interface_match"])
            statuses = {
                check["id"]: check["status"]
                for check in result.payload["checks"]
            }
            self.assertEqual(statuses["gateway.observed"], "WARN")

    def test_debug_selector_prefers_physical_wifi_over_tailscale(self):
        selected = _pick_interface(provider=SelectorProvider())
        self.assertEqual(selected, "Wi-Fi")


if __name__ == "__main__":
    unittest.main()
