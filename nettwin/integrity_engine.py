from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any

from nettwin import __version__
from nettwin.correlation_engine import analyze_correlations, write_correlations
from nettwin.evidence_engine import analyze_evidence, write_evidence
from nettwin.event_engine import analyze_events, write_events
from nettwin.fingerprint_engine import analyze_fingerprints, write_fingerprints
from nettwin.quality_engine import analyze_quality, write_quality


INTEGRITY_SCHEMA_VERSION = "run-integrity-v1"
CHECKSUM_FILENAME = "checksums.sha256"
METADATA_FILENAME = "run_metadata.json"
VERIFY_REPORT_FILENAME = "integrity_report.json"
REPRO_REPORT_FILENAME = "reproducibility_report.json"

# Archivos que son producto de la propia verificación y, por tanto, no deben
# formar parte del conjunto que se auto-verifica.
SELF_GENERATED_FILES = {
    CHECKSUM_FILENAME,
    VERIFY_REPORT_FILENAME,
    REPRO_REPORT_FILENAME,
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(
    r"^[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?$"
)


@dataclass(frozen=True)
class IntegrityConfig:
    run_id: str
    interface: str | None = None
    targets: tuple[str, ...] = ()
    requested_duration_seconds: float | None = None
    config_path: str | Path | None = None
    sensor_version: str = __version__
    analyzer_version: str = __version__

    def validate(self) -> None:
        run_id = self.run_id.strip()
        if not run_id:
            raise ValueError("run_id no puede estar vacío")
        if any(char in run_id for char in ("\n", "\r", "\t")):
            raise ValueError("run_id contiene caracteres de control no permitidos")
        if self.requested_duration_seconds is not None and self.requested_duration_seconds < 0:
            raise ValueError("requested_duration_seconds debe ser >= 0")
        if not self.sensor_version.strip():
            raise ValueError("sensor_version no puede estar vacío")
        if not self.analyzer_version.strip():
            raise ValueError("analyzer_version no puede estar vacío")


@dataclass(frozen=True)
class IntegrityBuildResult:
    metadata_path: Path
    checksums_path: Path
    metadata: dict[str, Any]
    tracked_files: tuple[str, ...]


@dataclass(frozen=True)
class IntegrityVerifyResult:
    valid: bool
    checked_files: int
    missing_files: tuple[str, ...]
    mismatched_files: tuple[str, ...]
    malformed_lines: tuple[str, ...]
    unsafe_paths: tuple[str, ...]
    untracked_files: tuple[str, ...]
    report_path: Path


@dataclass(frozen=True)
class ReproducibilityResult:
    reproducible: bool
    report_path: Path
    compared_artifacts: tuple[str, ...]
    differences: tuple[dict[str, Any], ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_posix(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    if "\n" in relative or "\r" in relative:
        raise ValueError(f"Nombre de archivo no soportado para checksums: {relative!r}")
    return relative


def _eligible_files(run_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in run_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                f"No se permiten enlaces simbólicos dentro del dataset de integridad: {path}"
            )
        if not path.is_file():
            continue
        if path.name in SELF_GENERATED_FILES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: _relative_posix(item, run_dir))


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _falsey(value: Any) -> bool:
    return str(value).strip().lower() in {"false", "0", "no", "n"}


def _profile_csv(path: Path) -> dict[str, Any]:
    rows = 0
    failures: int | None = None
    timestamps: list[datetime] = []
    interfaces: set[str] = set()
    targets: set[str] = set()
    status_column: str | None = None

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if "sample_status" in fields:
            status_column = "sample_status"
            failures = 0
        elif "sample_ok" in fields:
            status_column = "sample_ok"
            failures = 0

        for row in reader:
            rows += 1
            timestamp = _parse_timestamp(row.get("timestamp"))
            if timestamp is not None:
                timestamps.append(timestamp)
            interface = str(row.get("interface") or "").strip()
            if interface:
                interfaces.add(interface)
            target = str(row.get("target") or "").strip()
            if target:
                targets.add(target)
            if status_column == "sample_status":
                if str(row.get("sample_status") or "").strip().lower() != "ok":
                    failures = int(failures or 0) + 1
            elif status_column == "sample_ok":
                if _falsey(row.get("sample_ok")):
                    failures = int(failures or 0) + 1

    first = min(timestamps).isoformat() if timestamps else None
    last = max(timestamps).isoformat() if timestamps else None
    duration = (
        float((max(timestamps) - min(timestamps)).total_seconds())
        if len(timestamps) >= 2
        else 0.0 if timestamps else None
    )
    return {
        "rows": rows,
        "failed_rows": failures,
        "status_source": status_column,
        "first_timestamp": first,
        "last_timestamp": last,
        "duration_seconds": duration,
        "interfaces": sorted(interfaces),
        "targets": sorted(targets),
    }


def _is_sample_file(path: Path) -> bool:
    name = path.name.lower()
    return name in {
        "interface_samples.csv",
        "host_samples.csv",
        "probe_samples.csv",
    } or name.endswith("_samples.csv")


def _build_metadata(run_dir: Path, config: IntegrityConfig) -> dict[str, Any]:
    sample_profiles: dict[str, Any] = {}
    observed_times: list[datetime] = []
    discovered_interfaces: set[str] = set()
    discovered_targets: set[str] = set()
    total_samples = 0
    failed_known_total = 0
    failure_unknown_files: list[str] = []

    for path in _eligible_files(run_dir):
        if path.suffix.lower() != ".csv" or not _is_sample_file(path):
            continue
        relative = _relative_posix(path, run_dir)
        profile = _profile_csv(path)
        sample_profiles[relative] = profile
        total_samples += int(profile["rows"])
        if profile["failed_rows"] is None:
            failure_unknown_files.append(relative)
        else:
            failed_known_total += int(profile["failed_rows"])
        discovered_interfaces.update(profile["interfaces"])
        discovered_targets.update(profile["targets"])
        first = _parse_timestamp(profile["first_timestamp"])
        last = _parse_timestamp(profile["last_timestamp"])
        if first is not None:
            observed_times.append(first)
        if last is not None:
            observed_times.append(last)

    interface = config.interface.strip() if config.interface else None
    if interface is None and len(discovered_interfaces) == 1:
        interface = next(iter(discovered_interfaces))

    targets = sorted({str(item).strip() for item in config.targets if str(item).strip()} | discovered_targets)
    first_ts = min(observed_times).isoformat() if observed_times else None
    last_ts = max(observed_times).isoformat() if observed_times else None
    observed_duration = (
        float((max(observed_times) - min(observed_times)).total_seconds())
        if len(observed_times) >= 2
        else 0.0 if observed_times else None
    )

    configuration: dict[str, Any] | None = None
    if config.config_path is not None:
        config_path = Path(config.config_path)
        if not config_path.exists() or not config_path.is_file():
            raise ValueError(f"No existe el archivo de configuración: {config_path}")
        configuration = {
            "path": str(config_path),
            "sha256": _sha256(config_path),
            "size_bytes": int(config_path.stat().st_size),
        }

    metadata = {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "run_id": config.run_id.strip(),
        "software": {
            "sensor_version": config.sensor_version,
            "analyzer_version": config.analyzer_version,
        },
        "configuration": configuration,
        "observed_window": {
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "duration_seconds": observed_duration,
            "requested_duration_seconds": config.requested_duration_seconds,
        },
        "interface": interface,
        "discovered_interfaces": sorted(discovered_interfaces),
        "targets": targets,
        "sample_summary": {
            "total_rows": total_samples,
            "failed_rows_known_total": failed_known_total,
            "failure_status_unknown_files": sorted(failure_unknown_files),
            "files": sample_profiles,
        },
        "integrity": {
            "algorithm": "SHA-256",
            "checksums_file": CHECKSUM_FILENAME,
            "scope": "todos los archivos del run presentes al finalizar, excepto reportes auto-generados de integridad/reproducibilidad",
        },
        "limitations": [
            "Los checksums prueban integridad de bytes, no exactitud semántica de las mediciones.",
            "La duración observada se deriva de timestamps disponibles en archivos *_samples.csv.",
            "failed_rows solo se conoce cuando el CSV expone sample_status o sample_ok.",
        ],
    }
    return metadata


def finalize_integrity(
    run_dir: str | Path,
    *,
    config: IntegrityConfig,
) -> IntegrityBuildResult:
    config.validate()
    root = Path(run_dir)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"No existe el directorio de ejecución: {root}")

    metadata = _build_metadata(root, config)
    metadata_path = root / METADATA_FILENAME
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    tracked = _eligible_files(root)
    checksum_lines = [
        f"{_sha256(path)}  {_relative_posix(path, root)}"
        for path in tracked
    ]
    checksums_path = root / CHECKSUM_FILENAME
    checksums_path.write_text(
        "\n".join(checksum_lines) + ("\n" if checksum_lines else ""),
        encoding="utf-8",
    )
    return IntegrityBuildResult(
        metadata_path=metadata_path,
        checksums_path=checksums_path,
        metadata=metadata,
        tracked_files=tuple(_relative_posix(path, root) for path in tracked),
    )


def _safe_manifest_target(root: Path, relative: str) -> Path | None:
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    try:
        resolved_root = root.resolve()
        resolved = (root / candidate).resolve()
        resolved.relative_to(resolved_root)
    except (ValueError, OSError):
        return None
    return resolved


def verify_integrity(
    run_dir: str | Path,
    *,
    strict_untracked: bool = False,
) -> IntegrityVerifyResult:
    root = Path(run_dir)
    checksums_path = root / CHECKSUM_FILENAME
    if not root.exists() or not root.is_dir():
        raise ValueError(f"No existe el directorio de ejecución: {root}")
    if not checksums_path.exists():
        raise ValueError(f"No existe {CHECKSUM_FILENAME} en {root}")

    missing: list[str] = []
    mismatched: list[str] = []
    malformed: list[str] = []
    unsafe: list[str] = []
    tracked_names: set[str] = set()
    checked = 0

    for number, raw_line in enumerate(checksums_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        if "  " not in line:
            malformed.append(f"línea {number}: {line}")
            continue
        digest, relative = line.split("  ", 1)
        digest = digest.strip().lower()
        relative = relative.strip()
        if not _HEX64.fullmatch(digest) or not relative:
            malformed.append(f"línea {number}: {line}")
            continue
        if relative in tracked_names:
            malformed.append(f"línea {number}: ruta duplicada {relative}")
            continue
        tracked_names.add(relative)
        target = _safe_manifest_target(root, relative)
        if target is None:
            unsafe.append(relative)
            continue
        if not target.exists() or not target.is_file():
            missing.append(relative)
            continue
        if target.is_symlink():
            unsafe.append(relative)
            continue
        checked += 1
        if _sha256(target) != digest:
            mismatched.append(relative)

    current = {
        _relative_posix(path, root)
        for path in _eligible_files(root)
    }
    untracked = sorted(current - tracked_names)
    valid = not (missing or mismatched or malformed or unsafe)
    if strict_untracked and untracked:
        valid = False

    report = {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "mode": "verify-integrity",
        "valid": valid,
        "strict_untracked": strict_untracked,
        "checked_files": checked,
        "missing_files": sorted(missing),
        "mismatched_files": sorted(mismatched),
        "malformed_lines": malformed,
        "unsafe_paths": sorted(unsafe),
        "untracked_files": untracked,
        "checksums_sha256": _sha256(checksums_path),
    }
    report_path = root / VERIFY_REPORT_FILENAME
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return IntegrityVerifyResult(
        valid=valid,
        checked_files=checked,
        missing_files=tuple(sorted(missing)),
        mismatched_files=tuple(sorted(mismatched)),
        malformed_lines=tuple(malformed),
        unsafe_paths=tuple(sorted(unsafe)),
        untracked_files=tuple(untracked),
        report_path=report_path,
    )


def _parse_scalar(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if _INT.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            pass
    if _FLOAT.fullmatch(text):
        try:
            number = float(text)
            if math.isfinite(number):
                return number
        except ValueError:
            pass
    return text


def _read_csv_semantic(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: _parse_scalar(value) for key, value in row.items()}
            for row in reader
        ]


def _compare_semantic(
    left: Any,
    right: Any,
    *,
    abs_tol: float,
    rel_tol: float,
    path: str = "$",
    differences: list[dict[str, Any]],
) -> None:
    if isinstance(left, bool) or isinstance(right, bool):
        if left != right:
            differences.append({"path": path, "left": left, "right": right})
        return
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not math.isclose(float(left), float(right), abs_tol=abs_tol, rel_tol=rel_tol):
            differences.append({"path": path, "left": left, "right": right})
        return
    if isinstance(left, dict) and isinstance(right, dict):
        keys = sorted(set(left) | set(right))
        for key in keys:
            if key not in left or key not in right:
                differences.append({
                    "path": f"{path}.{key}",
                    "left": left.get(key),
                    "right": right.get(key),
                })
                continue
            _compare_semantic(
                left[key], right[key], abs_tol=abs_tol, rel_tol=rel_tol,
                path=f"{path}.{key}", differences=differences,
            )
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append({"path": path, "left_length": len(left), "right_length": len(right)})
            return
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            _compare_semantic(
                left_item, right_item, abs_tol=abs_tol, rel_tol=rel_tol,
                path=f"{path}[{index}]", differences=differences,
            )
        return
    if left != right:
        differences.append({"path": path, "left": left, "right": right})


def _run_analytics_pipeline(
    interface_csv: Path,
    probe_csv: Path,
    *,
    host_csv: Path | None,
    capacity_mbps: float | None,
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    quality_dir = output_dir / "quality"
    events_dir = output_dir / "events"
    correlations_dir = output_dir / "correlations"
    evidence_dir = output_dir / "evidence"
    fingerprints_dir = output_dir / "fingerprints"

    quality = analyze_quality(interface_csv, probe_csv, capacity_mbps=capacity_mbps)
    write_quality(quality, quality_dir)

    events = analyze_events(
        quality_dir / "quality_interface_processed.csv",
        quality_dir / "quality_probe_processed.csv",
    )
    events_json = write_events(events, events_dir)

    correlations = analyze_correlations(
        quality_dir / "quality_interface_processed.csv",
        quality_dir / "quality_probe_processed.csv",
        host_csv=host_csv,
    )
    correlations_json = write_correlations(correlations, correlations_dir)

    evidence = analyze_evidence(events_json, correlations_json)
    write_evidence(evidence, evidence_dir)

    fingerprints = analyze_fingerprints(events_json)
    write_fingerprints(fingerprints, fingerprints_dir)

    return {
        "quality_interface_summary.csv": quality_dir / "quality_interface_summary.csv",
        "quality_probe_summary.csv": quality_dir / "quality_probe_summary.csv",
        "event_summary.csv": events_dir / "event_summary.csv",
        "correlation_summary.csv": correlations_dir / "correlation_summary.csv",
        "evidence_summary.csv": evidence_dir / "evidence_summary.csv",
        "event_fingerprint_summary.csv": fingerprints_dir / "event_fingerprint_summary.csv",
    }


def check_reproducibility(
    interface_csv: str | Path,
    probe_csv: str | Path,
    *,
    output_dir: str | Path,
    host_csv: str | Path | None = None,
    capacity_mbps: float | None = None,
    abs_tolerance: float = 1e-9,
    rel_tolerance: float = 1e-9,
) -> ReproducibilityResult:
    if abs_tolerance < 0 or rel_tolerance < 0:
        raise ValueError("Las tolerancias deben ser >= 0")
    interface_path = Path(interface_csv)
    probe_path = Path(probe_csv)
    host_path = Path(host_csv) if host_csv is not None else None
    for label, path in (("interface_csv", interface_path), ("probe_csv", probe_path)):
        if not path.exists():
            raise ValueError(f"No existe {label}: {path}")
    if host_path is not None and not host_path.exists():
        raise ValueError(f"No existe host_csv: {host_path}")

    root = Path(output_dir)
    if root.exists():
        shutil.rmtree(root)
    run_a = _run_analytics_pipeline(
        interface_path, probe_path, host_csv=host_path,
        capacity_mbps=capacity_mbps, output_dir=root / "replay_a",
    )
    run_b = _run_analytics_pipeline(
        interface_path, probe_path, host_csv=host_path,
        capacity_mbps=capacity_mbps, output_dir=root / "replay_b",
    )

    all_differences: list[dict[str, Any]] = []
    artifact_report: dict[str, Any] = {}
    for name in sorted(run_a):
        left = _read_csv_semantic(run_a[name])
        right = _read_csv_semantic(run_b[name])
        differences: list[dict[str, Any]] = []
        _compare_semantic(
            left, right,
            abs_tol=abs_tolerance,
            rel_tol=rel_tolerance,
            differences=differences,
        )
        artifact_report[name] = {
            "reproducible": not differences,
            "difference_count": len(differences),
            "sha256_replay_a": _sha256(run_a[name]),
            "sha256_replay_b": _sha256(run_b[name]),
        }
        for difference in differences[:100]:
            all_differences.append({"artifact": name, **difference})

    inputs = {
        "interface_csv": {"path": str(interface_path), "sha256": _sha256(interface_path)},
        "probe_csv": {"path": str(probe_path), "sha256": _sha256(probe_path)},
        "host_csv": (
            {"path": str(host_path), "sha256": _sha256(host_path)}
            if host_path is not None else None
        ),
        "capacity_mbps": capacity_mbps,
    }
    reproducible = not all_differences
    report = {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "mode": "reproducibility-check",
        "reproducible": reproducible,
        "inputs": inputs,
        "numeric_tolerance": {
            "absolute": abs_tolerance,
            "relative": rel_tolerance,
            "comparison": "math.isclose para valores numéricos; igualdad exacta para campos no numéricos",
        },
        "compared_artifacts": artifact_report,
        "differences": all_differences,
        "limitations": [
            "La prueba demuestra reproducibilidad del pipeline bajo la misma versión de software y entorno de ejecución.",
            "No demuestra que los datos de entrada sean físicamente correctos; esa propiedad corresponde a calidad de medición.",
            "Se comparan artefactos agregados de Quality, Event, Correlation, Evidence y Fingerprint Engine.",
        ],
    }
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / REPRO_REPORT_FILENAME
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ReproducibilityResult(
        reproducible=reproducible,
        report_path=report_path,
        compared_artifacts=tuple(sorted(run_a)),
        differences=tuple(all_differences),
    )
