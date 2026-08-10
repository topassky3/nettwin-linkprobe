from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class DatasetQualityConfig:
    gap_factor: float = 1.75
    warning_coverage_pct: float = 95.0
    fail_coverage_pct: float = 80.0
    warning_valid_pct: float = 95.0
    fail_valid_pct: float = 80.0
    clock_tolerance_seconds: float = 5.0

    def validate(self) -> None:
        if self.gap_factor <= 1.0:
            raise ValueError("gap_factor debe ser > 1")
        for name in (
            "warning_coverage_pct", "fail_coverage_pct",
            "warning_valid_pct", "fail_valid_pct",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 100.0:
                raise ValueError(f"{name} debe estar entre 0 y 100")
        if self.fail_coverage_pct > self.warning_coverage_pct:
            raise ValueError("fail_coverage_pct debe ser <= warning_coverage_pct")
        if self.fail_valid_pct > self.warning_valid_pct:
            raise ValueError("fail_valid_pct debe ser <= warning_valid_pct")
        if self.clock_tolerance_seconds < 0:
            raise ValueError("clock_tolerance_seconds debe ser >= 0")


@dataclass(frozen=True)
class DatasetQualityResult:
    status: str
    safe_for_conclusions: bool
    summary: dict[str, Any]
    sources: dict[str, dict[str, Any]]
    checks: list[dict[str, Any]]
    limitations: list[str]
    config: DatasetQualityConfig


def _json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ValueError(f"No existe el archivo requerido: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON inválido en {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} debe contener un objeto JSON")
    return payload


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ValueError(f"No existe el archivo requerido: {path}")
    try:
        return pd.read_csv(path)
    except Exception as exc:
        raise ValueError(f"No se pudo leer {path.name}: {exc}") from exc


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "si", "sí"})


def _expected_cycles(duration_seconds: float, interval_seconds: float) -> int:
    if duration_seconds <= 0 or interval_seconds <= 0:
        return 0
    return int(math.ceil(duration_seconds / interval_seconds - 1e-12))


def _status_from_pct(value: float, warn_at: float, fail_at: float) -> str:
    if value < fail_at:
        return "FAIL"
    if value < warn_at:
        return "WARN"
    return "PASS"


def _timestamp_profile(
    frame: pd.DataFrame,
    *,
    interval_seconds: float,
    group_columns: list[str],
    duplicate_columns: list[str],
    gap_factor: float,
    window_start: pd.Timestamp | None,
    window_end: pd.Timestamp | None,
    clock_tolerance_seconds: float,
) -> dict[str, Any]:
    if "timestamp" not in frame.columns:
        return {
            "invalid_timestamps": len(frame),
            "out_of_order": 0,
            "duplicates": 0,
            "gaps": 0,
            "max_gap_seconds": None,
            "outside_run_window": 0,
            "first_timestamp": None,
            "last_timestamp": None,
        }

    parsed = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    invalid = int(parsed.isna().sum())
    valid_mask = parsed.notna()
    valid = frame.loc[valid_mask].copy()
    valid["_ts"] = parsed.loc[valid_mask]

    out_of_order = 0
    if not valid.empty:
        groups = [([], valid)] if not group_columns else list(valid.groupby(group_columns, sort=False, dropna=False))
        for _, group in groups:
            values = group["_ts"].tolist()
            out_of_order += sum(1 for left, right in zip(values, values[1:]) if right < left)

    duplicate_keys = [column for column in duplicate_columns if column in valid.columns]
    duplicates = int(valid.duplicated(subset=["_ts", *duplicate_keys], keep="first").sum()) if not valid.empty else 0

    gaps = 0
    max_gap: float | None = None
    if not valid.empty and interval_seconds > 0:
        groups = [([], valid)] if not group_columns else list(valid.groupby(group_columns, sort=False, dropna=False))
        threshold = interval_seconds * gap_factor
        for _, group in groups:
            ordered = group.sort_values("_ts")
            deltas = ordered["_ts"].diff().dt.total_seconds().dropna()
            if not deltas.empty:
                local_max = float(deltas.max())
                max_gap = local_max if max_gap is None else max(max_gap, local_max)
                gaps += int((deltas > threshold).sum())

    outside = 0
    if not valid.empty and window_start is not None and window_end is not None:
        tolerance = pd.to_timedelta(clock_tolerance_seconds, unit="s")
        outside = int(((valid["_ts"] < window_start - tolerance) | (valid["_ts"] > window_end + tolerance)).sum())

    first = valid["_ts"].min().isoformat() if not valid.empty else None
    last = valid["_ts"].max().isoformat() if not valid.empty else None
    return {
        "invalid_timestamps": invalid,
        "out_of_order": int(out_of_order),
        "duplicates": duplicates,
        "gaps": int(gaps),
        "max_gap_seconds": max_gap,
        "outside_run_window": outside,
        "first_timestamp": first,
        "last_timestamp": last,
    }


def _sample_status(frame: pd.DataFrame) -> tuple[int, int, float]:
    total = int(len(frame))
    if total == 0:
        return 0, 0, 0.0
    if "sample_status" not in frame.columns:
        return total, 0, 100.0
    ok = frame["sample_status"].astype(str).str.strip().str.lower().eq("ok")
    valid = int(ok.sum())
    errors = total - valid
    return valid, errors, (valid / total) * 100.0


def _negative_count(frame: pd.DataFrame, columns: list[str]) -> int:
    total = 0
    for column in columns:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        total += int((values < 0).sum())
    return total


def _source_profile(
    name: str,
    frame: pd.DataFrame,
    *,
    expected_rows: int,
    interval_seconds: float,
    timestamp_groups: list[str],
    duplicate_columns: list[str],
    gap_factor: float,
    window_start: pd.Timestamp | None,
    window_end: pd.Timestamp | None,
    clock_tolerance_seconds: float,
) -> dict[str, Any]:
    actual = int(len(frame))
    valid_rows, error_rows, valid_pct = _sample_status(frame)
    coverage_pct = 100.0 if expected_rows == 0 and actual == 0 else (
        0.0 if expected_rows <= 0 else min(actual, expected_rows) / expected_rows * 100.0
    )
    return {
        "source": name,
        "expected_rows": int(expected_rows),
        "actual_rows": actual,
        "valid_rows": valid_rows,
        "error_rows": error_rows,
        "coverage_pct": round(coverage_pct, 6),
        "valid_pct": round(valid_pct, 6),
        "timestamp_quality": _timestamp_profile(
            frame,
            interval_seconds=interval_seconds,
            group_columns=timestamp_groups,
            duplicate_columns=duplicate_columns,
            gap_factor=gap_factor,
            window_start=window_start,
            window_end=window_end,
            clock_tolerance_seconds=clock_tolerance_seconds,
        ),
    }


def assess_dataset_quality(
    run_dir: str | Path,
    *,
    integrity_valid: bool,
    config: DatasetQualityConfig | None = None,
) -> DatasetQualityResult:
    cfg = config or DatasetQualityConfig()
    cfg.validate()
    run = Path(run_dir)
    experiment = _json(run / "experiment_config.json")
    orchestration = _json(run / "orchestration.json", required=False)
    preflight = _json(run / "preflight.json", required=False)

    run_cfg = experiment.get("run", {}) if isinstance(experiment.get("run"), dict) else {}
    interface_cfg = experiment.get("interface", {}) if isinstance(experiment.get("interface"), dict) else {}
    host_cfg = experiment.get("host", {}) if isinstance(experiment.get("host"), dict) else {}
    probes_cfg = experiment.get("probes", {}) if isinstance(experiment.get("probes"), dict) else {}
    targets_cfg = experiment.get("targets", []) if isinstance(experiment.get("targets"), list) else []

    duration = _number(run_cfg.get("duration_seconds")) or 0.0
    interface_interval = _number(interface_cfg.get("interval_seconds")) or 0.0
    host_interval = _number(host_cfg.get("interval_seconds")) or interface_interval
    probe_interval = _number(probes_cfg.get("interval_seconds")) or interface_interval
    target_count = max(1, len(targets_cfg))

    interface = _read_csv(run / "interface_samples.csv")
    host = _read_csv(run / "host_samples.csv")
    probes = _read_csv(run / "probe_samples.csv")

    start = pd.to_datetime(orchestration.get("started_utc"), utc=True, errors="coerce")
    end = pd.to_datetime(orchestration.get("ended_utc"), utc=True, errors="coerce")
    window_start = None if pd.isna(start) else start
    window_end = None if pd.isna(end) else end

    sources = {
        "interface": _source_profile(
            "interface", interface,
            expected_rows=_expected_cycles(duration, interface_interval),
            interval_seconds=interface_interval,
            timestamp_groups=["interface"],
            duplicate_columns=["interface"],
            gap_factor=cfg.gap_factor,
            window_start=window_start,
            window_end=window_end,
            clock_tolerance_seconds=cfg.clock_tolerance_seconds,
        ),
        "host": _source_profile(
            "host", host,
            expected_rows=_expected_cycles(duration, host_interval),
            interval_seconds=host_interval,
            timestamp_groups=[],
            duplicate_columns=[],
            gap_factor=cfg.gap_factor,
            window_start=window_start,
            window_end=window_end,
            clock_tolerance_seconds=cfg.clock_tolerance_seconds,
        ),
        "probes": _source_profile(
            "probes", probes,
            expected_rows=_expected_cycles(duration, probe_interval) * target_count,
            interval_seconds=probe_interval,
            timestamp_groups=["target"],
            duplicate_columns=["target"],
            gap_factor=cfg.gap_factor,
            window_start=window_start,
            window_end=window_end,
            clock_tolerance_seconds=cfg.clock_tolerance_seconds,
        ),
    }

    expected_interface = str(interface_cfg.get("name", "")).strip()
    observed_interfaces = sorted(set(interface.get("interface", pd.Series(dtype=str)).dropna().astype(str)))
    interface_match = bool(expected_interface) and observed_interfaces == [expected_interface]

    impossible_interface = _negative_count(
        interface,
        [
            "rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors",
            "rx_drops", "tx_drops", "rx_bytes_delta", "tx_bytes_delta",
            "rx_packets_delta", "tx_packets_delta", "rx_errors_delta", "tx_errors_delta",
            "rx_drops_delta", "tx_drops_delta",
        ],
    )
    impossible_host = 0
    if "cpu_percent" in host:
        cpu = pd.to_numeric(host["cpu_percent"], errors="coerce")
        impossible_host += int(((cpu < 0) | (cpu > 100)).sum())
    if "memory_percent" in host:
        memory = pd.to_numeric(host["memory_percent"], errors="coerce")
        impossible_host += int(((memory < 0) | (memory > 100)).sum())
    for column in ("uptime_seconds", "uptime"):
        if column in host:
            uptime = pd.to_numeric(host[column], errors="coerce")
            impossible_host += int((uptime < 0).sum())

    impossible_probes = _negative_count(
        probes,
        ["packets_sent", "packets_received", "payload_bytes", "estimated_outbound_payload_bytes"],
    )
    if {"packets_sent", "packets_received"}.issubset(probes.columns):
        sent = pd.to_numeric(probes["packets_sent"], errors="coerce")
        received = pd.to_numeric(probes["packets_received"], errors="coerce")
        impossible_probes += int((received > sent).sum())
    if "packet_loss_pct" in probes:
        loss = pd.to_numeric(probes["packet_loss_pct"], errors="coerce")
        impossible_probes += int(((loss < 0) | (loss > 100)).sum())

    counter_resets = 0
    counter_overflows = 0
    if "counter_event" in interface:
        events = interface["counter_event"].astype(str).str.lower()
        counter_resets = int(events.str.contains("reset", regex=False).sum())
        counter_overflows = int(events.str.contains("overflow", regex=False).sum())

    unreachable_rows = 0
    if "reachability" in probes:
        unreachable_rows = int((~_truthy(probes["reachability"])).sum())

    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, detail: str, *, blocking: bool = False) -> None:
        checks.append({"id": check_id, "status": status, "blocking": blocking, "detail": detail})

    add(
        "integrity.strict",
        "PASS" if integrity_valid else "FAIL",
        "Integridad SHA-256 estricta válida." if integrity_valid else "La integridad SHA-256 estricta no es válida.",
        blocking=True,
    )
    add(
        "interface.expected",
        "PASS" if interface_match else "FAIL",
        f"Interfaz esperada={expected_interface or 'N/D'}; observadas={observed_interfaces or ['N/D']}.",
        blocking=True,
    )

    for name, profile in sources.items():
        coverage_status = _status_from_pct(
            float(profile["coverage_pct"]), cfg.warning_coverage_pct, cfg.fail_coverage_pct
        )
        valid_status = _status_from_pct(
            float(profile["valid_pct"]), cfg.warning_valid_pct, cfg.fail_valid_pct
        )
        add(
            f"{name}.coverage",
            coverage_status,
            f"{profile['actual_rows']}/{profile['expected_rows']} filas; cobertura={profile['coverage_pct']:.2f}%.",
            blocking=coverage_status == "FAIL",
        )
        add(
            f"{name}.valid_samples",
            valid_status,
            f"{profile['valid_rows']}/{profile['actual_rows']} filas válidas; validez={profile['valid_pct']:.2f}%.",
            blocking=valid_status == "FAIL",
        )
        ts = profile["timestamp_quality"]
        timestamp_status = "FAIL" if ts["invalid_timestamps"] else (
            "WARN" if ts["out_of_order"] or ts["duplicates"] or ts["outside_run_window"] else "PASS"
        )
        add(
            f"{name}.timestamps",
            timestamp_status,
            (
                f"inválidos={ts['invalid_timestamps']}, fuera_de_orden={ts['out_of_order']}, "
                f"duplicados={ts['duplicates']}, fuera_de_ventana={ts['outside_run_window']}."
            ),
            blocking=bool(ts["invalid_timestamps"]),
        )
        add(
            f"{name}.gaps",
            "WARN" if ts["gaps"] else "PASS",
            f"huecos={ts['gaps']}; máximo_gap_s={ts['max_gap_seconds'] if ts['max_gap_seconds'] is not None else 'N/D'}.",
            blocking=False,
        )

    impossible_total = impossible_interface + impossible_host + impossible_probes
    add(
        "values.possible",
        "FAIL" if impossible_total else "PASS",
        (
            f"valores_imposibles={impossible_total} "
            f"(interface={impossible_interface}, host={impossible_host}, probes={impossible_probes})."
        ),
        blocking=bool(impossible_total),
    )
    add(
        "interface.counter_resets",
        "WARN" if counter_resets else "PASS",
        f"resets={counter_resets}; overflows={counter_overflows}.",
        blocking=False,
    )
    add(
        "probes.reachability",
        "WARN" if unreachable_rows else "PASS",
        f"muestras_no_alcanzables={unreachable_rows}.",
        blocking=False,
    )
    timezone_name = (
        preflight.get("system", {}).get("timezone_name")
        if isinstance(preflight.get("system"), dict)
        else None
    )
    add(
        "clock.context",
        "PASS" if timezone_name and window_start is not None and window_end is not None else "WARN",
        (
            f"timezone={timezone_name or 'N/D'}; ventana_orquestada="
            f"{window_start.isoformat() if window_start is not None else 'N/D'}.."
            f"{window_end.isoformat() if window_end is not None else 'N/D'}."
        ),
        blocking=False,
    )

    blocking_fail = any(row["status"] == "FAIL" and row["blocking"] for row in checks)
    any_fail = any(row["status"] == "FAIL" for row in checks)
    any_warn = any(row["status"] == "WARN" for row in checks)
    status = "FAIL" if any_fail else ("WARN" if any_warn else "PASS")
    safe = not blocking_fail and status != "FAIL"

    expected_total = sum(int(profile["expected_rows"]) for profile in sources.values())
    actual_total = sum(int(profile["actual_rows"]) for profile in sources.values())
    valid_total = sum(int(profile["valid_rows"]) for profile in sources.values())
    temporal_integrity_pct = (
        100.0 if expected_total == 0 and actual_total == 0
        else (0.0 if expected_total <= 0 else min(valid_total, expected_total) / expected_total * 100.0)
    )
    summary = {
        "expected_rows_total": expected_total,
        "actual_rows_total": actual_total,
        "valid_rows_total": valid_total,
        "temporal_integrity_pct": round(temporal_integrity_pct, 6),
        "gaps_total": sum(int(profile["timestamp_quality"]["gaps"]) for profile in sources.values()),
        "duplicates_total": sum(int(profile["timestamp_quality"]["duplicates"]) for profile in sources.values()),
        "out_of_order_total": sum(int(profile["timestamp_quality"]["out_of_order"]) for profile in sources.values()),
        "invalid_timestamps_total": sum(int(profile["timestamp_quality"]["invalid_timestamps"]) for profile in sources.values()),
        "counter_resets": counter_resets,
        "counter_overflows": counter_overflows,
        "impossible_values_total": impossible_total,
        "unreachable_probe_rows": unreachable_rows,
        "expected_interface": expected_interface or None,
        "observed_interfaces": observed_interfaces,
    }
    limitations = [
        "La calidad del dataset describe únicamente esta ejecución y no la disponibilidad histórica del servicio.",
        "Un target no alcanzable se reporta como condición observada; no identifica por sí solo la causa física.",
        "Los huecos se detectan comparando deltas temporales con el intervalo configurado multiplicado por gap_factor.",
        "La verificación de reloj comprueba coherencia con la ventana orquestada y conserva el contexto de zona horaria del preflight.",
    ]
    return DatasetQualityResult(
        status=status,
        safe_for_conclusions=safe,
        summary=summary,
        sources=sources,
        checks=checks,
        limitations=limitations,
        config=cfg,
    )


def write_dataset_quality(result: DatasetQualityResult, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "dataset_quality.json"
    csv_path = output / "dataset_quality_summary.csv"
    payload = {
        "schema_version": "dataset-quality-v1",
        "status": result.status,
        "safe_for_conclusions": result.safe_for_conclusions,
        "summary": result.summary,
        "sources": result.sources,
        "checks": result.checks,
        "config": asdict(result.config),
        "limitations": result.limitations,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    fields = [
        "source", "expected_rows", "actual_rows", "valid_rows", "error_rows",
        "coverage_pct", "valid_pct", "invalid_timestamps", "out_of_order",
        "duplicates", "gaps", "max_gap_seconds", "outside_run_window",
        "first_timestamp", "last_timestamp",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name in ("interface", "host", "probes"):
            profile = result.sources[name]
            ts = profile["timestamp_quality"]
            writer.writerow({
                "source": name,
                "expected_rows": profile["expected_rows"],
                "actual_rows": profile["actual_rows"],
                "valid_rows": profile["valid_rows"],
                "error_rows": profile["error_rows"],
                "coverage_pct": profile["coverage_pct"],
                "valid_pct": profile["valid_pct"],
                **{key: ts.get(key) for key in fields if key in ts},
            })
    return json_path
