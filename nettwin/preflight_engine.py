from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ctypes
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
from typing import Any, Callable, Sequence

import psutil

from nettwin import __version__


PREFLIGHT_SCHEMA_VERSION = "preflight-v1"
DEFAULT_MINIMUM_FREE_DISK_MB = 50.0
MAX_RECOMMENDED_TARGETS = 3

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


@dataclass(frozen=True)
class PreflightTarget:
    name: str
    address: str
    role: str = "unspecified"


@dataclass(frozen=True)
class ExperimentConfig:
    config_path: Path
    config_sha256: str
    run_id: str
    duration_seconds: float
    interface_name: str
    interface_interval_seconds: float
    capacity_mbps: float | None
    host_interval_seconds: float
    probe_interval_seconds: float
    probe_timeout_ms: int
    probe_payload_bytes: int
    probe_count_per_target: int
    targets: tuple[PreflightTarget, ...]
    output_directory: Path
    minimum_free_disk_mb: float


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PreflightResult:
    payload: dict[str, Any]
    output_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} debe ser numérico")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} debe ser numérico") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} debe ser finito")
    if positive and number <= 0:
        raise ValueError(f"{label} debe ser > 0")
    return number


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    number = _number(value, label, positive=positive)
    if not number.is_integer():
        raise ValueError(f"{label} debe ser entero")
    return int(number)


def _require_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Falta objeto requerido '{key}' en la configuración")
    return value


def _validate_target(target: PreflightTarget) -> str:
    if not target.name.strip():
        raise ValueError("Cada target debe tener name no vacío")
    address = target.address.strip()
    if not address:
        raise ValueError(f"Target '{target.name}' debe tener address")
    if any(character.isspace() for character in address):
        raise ValueError(f"Target '{target.name}' contiene espacios en address")
    if "://" in address or "/" in address or ":" in address and address.count(":") == 1:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ValueError(
                f"Target '{target.name}' debe ser una IP o hostname sin esquema, ruta ni puerto"
            )
    try:
        parsed = ipaddress.ip_address(address)
        return "ipv4" if parsed.version == 4 else "ipv6"
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(address):
            raise ValueError(
                f"Target '{target.name}' tiene address inválido: {address!r}"
            )
        return "hostname"


def load_experiment_config(config_path: str | Path) -> ExperimentConfig:
    path = Path(config_path)
    if not path.exists() or not path.is_file():
        raise ValueError(f"No existe el archivo de configuración: {path}")
    if path.suffix.lower() != ".json":
        raise ValueError(
            "Fase 11 acepta configuración JSON para evitar dependencias adicionales; use un archivo .json"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido en {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("La raíz de la configuración debe ser un objeto JSON")

    run = _require_dict(payload, "run")
    interface = _require_dict(payload, "interface")
    host = _require_dict(payload, "host")
    probes = _require_dict(payload, "probes")
    output = _require_dict(payload, "output")

    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run.run_id es requerido")
    duration_seconds = _number(
        run.get("duration_seconds"), "run.duration_seconds", positive=True
    )

    interface_name = str(interface.get("name") or "").strip()
    if not interface_name:
        raise ValueError("interface.name es requerido")
    interface_interval = _number(
        interface.get("interval_seconds"), "interface.interval_seconds", positive=True
    )
    capacity_raw = interface.get("capacity_mbps")
    capacity_mbps = (
        None
        if capacity_raw is None
        else _number(capacity_raw, "interface.capacity_mbps", positive=True)
    )

    host_interval = _number(
        host.get("interval_seconds"), "host.interval_seconds", positive=True
    )
    probe_interval = _number(
        probes.get("interval_seconds"), "probes.interval_seconds", positive=True
    )
    probe_timeout_ms = _integer(
        probes.get("timeout_ms"), "probes.timeout_ms", positive=True
    )
    probe_payload_bytes = _integer(
        probes.get("payload_bytes"), "probes.payload_bytes", positive=True
    )
    if probe_payload_bytes > 1400:
        raise ValueError("probes.payload_bytes debe ser <= 1400")
    probe_count_per_target = _integer(
        probes.get("count_per_target"), "probes.count_per_target", positive=True
    )

    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("targets debe ser una lista no vacía")
    targets: list[PreflightTarget] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_targets, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"targets[{index}] debe ser un objeto")
        target = PreflightTarget(
            name=str(raw.get("name") or "").strip(),
            address=str(raw.get("address") or "").strip(),
            role=str(raw.get("role") or "unspecified").strip() or "unspecified",
        )
        _validate_target(target)
        if target.name in names:
            raise ValueError(f"Nombre de target duplicado: {target.name}")
        names.add(target.name)
        targets.append(target)

    output_raw = str(output.get("directory") or "").strip()
    if not output_raw:
        raise ValueError("output.directory es requerido")
    minimum_free_disk_mb = _number(
        output.get("minimum_free_disk_mb", DEFAULT_MINIMUM_FREE_DISK_MB),
        "output.minimum_free_disk_mb",
        positive=True,
    )

    base = path.resolve().parent
    output_directory = Path(output_raw)
    if not output_directory.is_absolute():
        output_directory = (base / output_directory).resolve()

    return ExperimentConfig(
        config_path=path.resolve(),
        config_sha256=_sha256(path),
        run_id=run_id,
        duration_seconds=duration_seconds,
        interface_name=interface_name,
        interface_interval_seconds=interface_interval,
        capacity_mbps=capacity_mbps,
        host_interval_seconds=host_interval,
        probe_interval_seconds=probe_interval,
        probe_timeout_ms=probe_timeout_ms,
        probe_payload_bytes=probe_payload_bytes,
        probe_count_per_target=probe_count_per_target,
        targets=tuple(targets),
        output_directory=output_directory,
        minimum_free_disk_mb=minimum_free_disk_mb,
    )


def _default_runner(command: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(command), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=3.0, check=False, shell=False,
    )
    return CommandResult(
        returncode=int(completed.returncode), stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _clock_snapshot() -> dict[str, Any]:
    local = datetime.now().astimezone()
    utc = datetime.now(timezone.utc)
    offset = local.utcoffset()
    return {
        "local_time": local.isoformat(), "utc_time": utc.isoformat(),
        "timezone_name": local.tzname(),
        "utc_offset_seconds": None if offset is None else int(offset.total_seconds()),
    }


def _elevation_status(system_name: str) -> bool | None:
    try:
        if system_name.lower().startswith("win"):
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        if hasattr(os, "geteuid"):
            return os.geteuid() == 0
    except Exception:
        return None
    return None


def _address_family_name(family: Any) -> str | None:
    if family == socket.AF_INET:
        return "ipv4"
    if family == socket.AF_INET6:
        return "ipv6"
    return None


def _interface_snapshot(
    interface_name: str, *, provider: Any = psutil,
    redact_local_addresses: bool = False,
) -> dict[str, Any]:
    stats = provider.net_if_stats()
    counters = provider.net_io_counters(pernic=True)
    addresses = provider.net_if_addrs()
    exists = interface_name in stats or interface_name in counters or interface_name in addresses
    stat = stats.get(interface_name)
    address_rows: list[dict[str, Any]] = []
    for item in addresses.get(interface_name, []):
        family = _address_family_name(getattr(item, "family", None))
        if family is None:
            continue
        address = str(getattr(item, "address", "") or "").split("%", 1)[0]
        if not address:
            continue
        if redact_local_addresses:
            address_value = None
            address_sha256 = hashlib.sha256(address.encode("utf-8")).hexdigest()
        else:
            address_value = address
            address_sha256 = None
        address_rows.append({
            "family": family, "address": address_value,
            "address_sha256": address_sha256,
            "netmask": None if redact_local_addresses else getattr(item, "netmask", None),
        })
    return {
        "selected": interface_name, "exists": exists,
        "is_up": None if stat is None else bool(stat.isup),
        "has_counters": interface_name in counters,
        "mtu": None if stat is None else int(stat.mtu),
        "reported_link_speed_mbps": None if stat is None or getattr(stat, "speed", -1) < 0 else int(stat.speed),
        "addresses": address_rows,
        "local_addresses_redacted": bool(redact_local_addresses),
    }


def _local_ipv4s(interface_snapshot: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for row in interface_snapshot.get("addresses", []):
        if row.get("family") == "ipv4" and row.get("address"):
            values.add(str(row["address"]))
    return values


def _parse_linux_gateway(output: str, interface_name: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default":
            continue
        gateway = None
        interface = None
        metric = None
        if "via" in parts:
            index = parts.index("via")
            if index + 1 < len(parts):
                gateway = parts[index + 1]
        if "dev" in parts:
            index = parts.index("dev")
            if index + 1 < len(parts):
                interface = parts[index + 1]
        if "metric" in parts:
            index = parts.index("metric")
            if index + 1 < len(parts):
                try:
                    metric = int(parts[index + 1])
                except ValueError:
                    metric = None
        candidates.append({"gateway": gateway, "interface": interface, "metric": metric})
    if not candidates:
        return None
    matching = [row for row in candidates if row["interface"] == interface_name]
    pool = matching or candidates
    return sorted(pool, key=lambda row: (row["metric"] is None, row["metric"] if row["metric"] is not None else 2**31))[0]


def _parse_windows_gateway(output: str, local_ipv4s: set[str]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    pattern = re.compile(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\S+)\s+(\S+)\s+(\d+)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        gateway, interface_ip, metric_raw = match.groups()
        candidates.append({"gateway": gateway, "interface_ip": interface_ip, "metric": int(metric_raw)})
    if not candidates:
        return None
    matching = [row for row in candidates if not local_ipv4s or row["interface_ip"] in local_ipv4s]
    pool = matching or candidates
    selected = sorted(pool, key=lambda row: row["metric"])[0]
    return {"gateway": selected["gateway"], "interface": None, "interface_ip": selected["interface_ip"], "metric": selected["metric"]}


def _parse_darwin_gateway(output: str) -> dict[str, Any] | None:
    gateway = None
    interface = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("gateway:"):
            gateway = stripped.split(":", 1)[1].strip() or None
        elif stripped.startswith("interface:"):
            interface = stripped.split(":", 1)[1].strip() or None
    if gateway is None and interface is None:
        return None
    return {"gateway": gateway, "interface": interface, "metric": None}


def _gateway_snapshot(
    interface_name: str, interface_snapshot: dict[str, Any], *, system_name: str,
    which_fn: Callable[[str], str | None] = shutil.which,
    runner: Callable[[Sequence[str]], CommandResult] = _default_runner,
) -> dict[str, Any]:
    lowered = system_name.lower()
    command: list[str] | None = None
    tool: str | None = None
    parser: Callable[[str], dict[str, Any] | None]
    if lowered.startswith("win"):
        tool = "route"
        command = ["route", "PRINT", "-4", "0.0.0.0"]
        parser = lambda text: _parse_windows_gateway(text, _local_ipv4s(interface_snapshot))
    elif lowered == "linux":
        tool = "ip"
        command = ["ip", "-4", "route", "show", "default"]
        parser = lambda text: _parse_linux_gateway(text, interface_name)
    elif lowered == "darwin":
        tool = "route"
        command = ["route", "-n", "get", "default"]
        parser = _parse_darwin_gateway
    else:
        return {"status": "unavailable", "gateway": None, "interface": None, "source": "unsupported_platform", "tool": None, "tool_path": None, "command": None, "command_was_read_only": True, "error": f"Plataforma no soportada para inspección de gateway: {system_name}"}
    tool_path = which_fn(tool)
    if not tool_path:
        return {"status": "unavailable", "gateway": None, "interface": None, "source": "read_only_command", "tool": tool, "tool_path": None, "command": command, "command_was_read_only": True, "error": f"No se encontró herramienta de inspección '{tool}'"}
    try:
        result = runner(command)
    except Exception as exc:
        return {"status": "unavailable", "gateway": None, "interface": None, "source": "read_only_command", "tool": tool, "tool_path": tool_path, "command": command, "command_was_read_only": True, "error": str(exc)}
    if result.returncode != 0:
        return {"status": "unavailable", "gateway": None, "interface": None, "source": "read_only_command", "tool": tool, "tool_path": tool_path, "command": command, "command_was_read_only": True, "error": (result.stderr or result.stdout).strip() or f"Comando terminó con código {result.returncode}"}
    parsed = parser(result.stdout)
    if parsed is None:
        return {"status": "unavailable", "gateway": None, "interface": None, "source": "read_only_command", "tool": tool, "tool_path": tool_path, "command": command, "command_was_read_only": True, "error": "No se pudo identificar gateway predeterminado en la salida"}
    return {"status": "available", **parsed, "source": "read_only_command", "tool": tool, "tool_path": tool_path, "command": command, "command_was_read_only": True, "error": None}


def _find_disk_probe_path(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return candidate


def _output_and_disk_snapshot(
    output_directory: Path, minimum_free_disk_mb: float, *,
    disk_usage_fn: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    parent = output_directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    write_probe = parent / ".nettwin_preflight_write_test"
    writable = False
    write_error = None
    try:
        write_probe.write_text("preflight", encoding="utf-8")
        write_probe.unlink()
        writable = True
    except Exception as exc:
        write_error = str(exc)
        try:
            if write_probe.exists():
                write_probe.unlink()
        except Exception:
            pass
    disk_path = _find_disk_probe_path(output_directory)
    usage = disk_usage_fn(disk_path)
    minimum_required_bytes = int(minimum_free_disk_mb * 1024 * 1024)
    free_bytes = int(usage.free)
    return {
        "output_directory": str(output_directory), "parent_directory": str(parent.resolve()),
        "output_writable": writable, "write_error": write_error,
        "disk_probe_path": str(disk_path), "total_bytes": int(usage.total),
        "used_bytes": int(usage.used), "free_bytes": free_bytes,
        "minimum_free_disk_mb": minimum_free_disk_mb,
        "minimum_required_bytes": minimum_required_bytes,
        "enough_free_space": free_bytes >= minimum_required_bytes,
    }


def _planned_cycles(duration_seconds: float, interval_seconds: float) -> int:
    return int(math.ceil(duration_seconds / interval_seconds))


def _experiment_estimate(config: ExperimentConfig) -> dict[str, Any]:
    interface_cycles = _planned_cycles(config.duration_seconds, config.interface_interval_seconds)
    host_cycles = _planned_cycles(config.duration_seconds, config.host_interval_seconds)
    probe_cycles = _planned_cycles(config.duration_seconds, config.probe_interval_seconds)
    probe_rows = probe_cycles * len(config.targets)
    payload_bytes = probe_rows * config.probe_count_per_target * config.probe_payload_bytes
    return {
        "duration_seconds": config.duration_seconds,
        "capacity_mbps": config.capacity_mbps,
        "intervals_seconds": {"interface": config.interface_interval_seconds, "host": config.host_interval_seconds, "probes": config.probe_interval_seconds},
        "expected_samples": {"interface_rows": interface_cycles, "host_rows": host_cycles, "probe_cycles_per_target": probe_cycles, "probe_rows_total": probe_rows, "total_rows": interface_cycles + host_cycles + probe_rows},
        "probe_traffic_estimate": {"targets": len(config.targets), "count_per_target_per_cycle": config.probe_count_per_target, "payload_bytes_per_echo": config.probe_payload_bytes, "estimated_outbound_payload_bytes": payload_bytes, "estimated_outbound_payload_kib": payload_bytes / 1024.0, "note": "Estimación de payload ICMP saliente únicamente; no incluye cabeceras Ethernet/IP/ICMP ni tráfico de respuesta."},
    }


def _check(check_id: str, status: str, detail: str, *, required: bool) -> dict[str, Any]:
    if status not in {"PASS", "WARN", "FAIL"}:
        raise ValueError(f"Estado de check inválido: {status}")
    return {"id": check_id, "status": status, "required": required, "detail": detail}


def run_preflight(
    config_path: str | Path, *, output_path: str | Path | None = None,
    include_hostname: bool = False, redact_local_addresses: bool = False,
    sensor_version: str = __version__, provider: Any = psutil,
    which_fn: Callable[[str], str | None] = shutil.which,
    runner: Callable[[Sequence[str]], CommandResult] = _default_runner,
    disk_usage_fn: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
    clock_fn: Callable[[], dict[str, Any]] = _clock_snapshot,
    hostname_fn: Callable[[], str] = socket.gethostname,
    system_name: str | None = None,
) -> PreflightResult:
    config = load_experiment_config(config_path)
    system_name = system_name or platform.system()
    interface = _interface_snapshot(config.interface_name, provider=provider, redact_local_addresses=redact_local_addresses)
    gateway = _gateway_snapshot(config.interface_name, interface, system_name=system_name, which_fn=which_fn, runner=runner)
    output_disk = _output_and_disk_snapshot(config.output_directory, config.minimum_free_disk_mb, disk_usage_fn=disk_usage_fn)
    ping_path = which_fn("ping")
    clock = clock_fn()
    hostname = hostname_fn()
    salted_hostname_hash = hashlib.sha256((config.run_id + "\0" + hostname).encode("utf-8")).hexdigest()
    elevated = _elevation_status(system_name)
    targets = [{**asdict(target), "address_type": _validate_target(target), "syntax_valid": True, "dns_resolution_performed": False, "active_probe_performed": False} for target in config.targets]

    checks: list[dict[str, Any]] = []
    checks.append(_check("interface.exists", "PASS" if interface["exists"] else "FAIL", f"Interfaz '{config.interface_name}' encontrada." if interface["exists"] else f"Interfaz '{config.interface_name}' no existe en el host.", required=True))
    checks.append(_check("interface.up", "PASS" if interface["is_up"] is True else "FAIL", "Interfaz reportada UP." if interface["is_up"] is True else f"Estado de interfaz no apto: {interface['is_up']!r}.", required=True))
    checks.append(_check("interface.counters", "PASS" if interface["has_counters"] else "FAIL", "Contadores RX/TX disponibles." if interface["has_counters"] else "No hay contadores RX/TX para la interfaz seleccionada.", required=True))
    checks.append(_check("interface.local_address", "PASS" if interface["addresses"] else "WARN", "Se observaron direcciones IP en la interfaz." if interface["addresses"] else "No se observaron direcciones IPv4/IPv6 en la interfaz.", required=False))
    checks.append(_check("interface.reported_speed", "PASS" if interface["reported_link_speed_mbps"] is not None else "WARN", f"Velocidad reportada por NIC: {interface['reported_link_speed_mbps']} Mbps; no se usa como capacidad contractual del enlace." if interface["reported_link_speed_mbps"] is not None else "Velocidad reportada por NIC no disponible; no bloquea la medición.", required=False))
    checks.append(_check("gateway.observed", "PASS" if gateway["status"] == "available" else "WARN", f"Gateway observado: {gateway.get('gateway') or 'N/D'}." if gateway["status"] == "available" else f"Gateway N/D: {gateway.get('error') or 'sin detalle'}.", required=False))
    checks.append(_check("tool.ping", "PASS" if ping_path else "FAIL", f"Ejecutable ping disponible: {ping_path}." if ping_path else "No se encontró el ejecutable ping requerido por Active Probe Engine.", required=True))
    checks.append(_check("output.writable", "PASS" if output_disk["output_writable"] else "FAIL", "Directorio padre de salida escribible." if output_disk["output_writable"] else f"No se puede escribir salida: {output_disk['write_error']}", required=True))
    checks.append(_check("disk.free_space", "PASS" if output_disk["enough_free_space"] else "FAIL", f"Espacio libre suficiente: {output_disk['free_bytes']} bytes." if output_disk["enough_free_space"] else f"Espacio libre insuficiente: {output_disk['free_bytes']} bytes; mínimo configurado {output_disk['minimum_required_bytes']} bytes.", required=True))
    checks.append(_check("targets.count", "PASS" if len(config.targets) <= MAX_RECOMMENDED_TARGETS else "WARN", f"{len(config.targets)} target(s) configurado(s)." if len(config.targets) <= MAX_RECOMMENDED_TARGETS else f"{len(config.targets)} targets configurados; el plan recomienda hasta {MAX_RECOMMENDED_TARGETS} referencias para el piloto.", required=False))
    checks.append(_check("targets.syntax", "PASS", f"{len(config.targets)} target(s) con sintaxis válida; no se enviaron sondas.", required=True))
    checks.append(_check("clock.timezone", "PASS" if clock.get("utc_time") and clock.get("local_time") else "FAIL", f"Hora local {clock.get('local_time')}; zona {clock.get('timezone_name') or 'N/D'}.", required=True))

    failures = [item for item in checks if item["status"] == "FAIL"]
    warnings = [item for item in checks if item["status"] == "WARN"]
    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    payload: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION, "mode": "preflight", "scope": "observational_only",
        "status": status, "ready_for_run": not failures, "run_id": config.run_id,
        "software": {"sensor_version": sensor_version, "python_version": sys.version.split()[0], "psutil_version": getattr(psutil, "__version__", None)},
        "configuration": {"path": str(config.config_path), "sha256": config.config_sha256, "format": "json"},
        "system": {"os": system_name, "release": platform.release(), "version": platform.version(), "machine": platform.machine(), **clock, "hostname_policy": "raw_and_per_run_sha256" if include_hostname else "per_run_sha256_only", "hostname": hostname if include_hostname else None, "hostname_sha256": salted_hostname_hash, "hostname_hash_scope": "run_id_salted"},
        "interface": interface,
        "gateway": gateway,
        "permissions": {"is_elevated": elevated, "elevation_required_by_linkprobe": False, "raw_socket_required": False, "output_writable": output_disk["output_writable"], "note": "LinkProbe usa el ejecutable ping del sistema; no abre raw sockets directamente."},
        "tools": {"ping": {"required": True, "available": bool(ping_path), "path": ping_path}, "route_inspection": {"required": False, "available": gateway.get("tool_path") is not None, "tool": gateway.get("tool"), "path": gateway.get("tool_path")}},
        "targets": targets,
        "disk": output_disk,
        "experiment": _experiment_estimate(config),
        "checks": checks,
        "check_summary": {"pass": sum(item["status"] == "PASS" for item in checks), "warn": len(warnings), "fail": len(failures)},
        "network_safety": {"active_probe_performed": False, "dns_resolution_performed": False, "routes_modified": False, "firewall_modified": False, "interfaces_modified": False, "services_modified": False, "network_configuration_modified": False, "read_only_commands": [gateway["command"]] if gateway.get("command") is not None else []},
        "limitations": ["Preflight no demuestra reachability de los targets porque no envía ICMP.", "Preflight no resuelve DNS; los hostnames se validan únicamente por sintaxis.", "La detección de gateway es best-effort y usa comandos de solo lectura del sistema.", "La velocidad reportada por la NIC no se interpreta como capacidad del servicio ISP.", "El estado PASS/WARN/FAIL describe preparación operacional previa, no calidad del enlace."],
    }
    target_output = Path(output_path) if output_path is not None else config.output_directory / "preflight.json"
    if not target_output.is_absolute():
        target_output = target_output.resolve()
    target_output.parent.mkdir(parents=True, exist_ok=True)
    target_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return PreflightResult(payload=payload, output_path=target_output)
