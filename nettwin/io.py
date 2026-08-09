from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "timestamp",
    "link_id",
    "rx_mbps",
    "tx_mbps",
    "capacity_mbps",
}
OPTIONAL_COLUMNS = {
    "latency_ms",
    "packet_loss_pct",
    "availability_pct",
}
NUMERIC_COLUMNS = {
    "rx_mbps",
    "tx_mbps",
    "capacity_mbps",
    "latency_ms",
    "packet_loss_pct",
    "availability_pct",
}

COLUMN_ALIASES: dict[str, set[str]] = {
    "timestamp": {
        "timestamp", "fecha", "fecha_hora", "datetime", "date_time", "time", "hora_fecha",
    },
    "link_id": {
        "link_id", "enlace", "enlace_id", "link", "interface", "interfaz", "device", "dispositivo",
    },
    "rx_mbps": {
        "rx_mbps", "rx", "download_mbps", "down_mbps", "in_mbps", "trafico_rx_mbps",
        "traffic_in_mbps", "entrada_mbps",
    },
    "tx_mbps": {
        "tx_mbps", "tx", "upload_mbps", "up_mbps", "out_mbps", "trafico_tx_mbps",
        "traffic_out_mbps", "salida_mbps",
    },
    "capacity_mbps": {
        "capacity_mbps", "capacity", "capacidad_mbps", "capacidad", "bandwidth_mbps", "ancho_banda_mbps",
    },
    "latency_ms": {
        "latency_ms", "latency", "latencia_ms", "latencia", "rtt_ms", "ping_ms",
    },
    "packet_loss_pct": {
        "packet_loss_pct", "packet_loss", "loss_pct", "perdida_pct", "perdida_paquetes_pct",
    },
    "availability_pct": {
        "availability_pct", "availability", "disponibilidad_pct", "disponibilidad", "uptime_pct",
    },
}


@dataclass(frozen=True)
class ValidationResult:
    data: pd.DataFrame
    rejected: pd.DataFrame
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    mapped_columns: dict[str, str]
    warnings: tuple[str, ...]


class DataValidationError(ValueError):
    """Error de estructura que impide analizar el archivo."""


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _load_manual_mapping(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    mapping_path = Path(path)
    try:
        raw = json.loads(mapping_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataValidationError(f"No existe el archivo de mapeo: {mapping_path}") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"JSON inválido en el mapeo: {exc}") from exc
    if not isinstance(raw, dict):
        raise DataValidationError("El mapeo debe ser un objeto JSON: columna_origen -> columna_canonica.")

    allowed_targets = REQUIRED_COLUMNS | OPTIONAL_COLUMNS
    mapping: dict[str, str] = {}
    for source, target in raw.items():
        source_norm = _normalize_name(source)
        target_norm = _normalize_name(target)
        if target_norm not in allowed_targets:
            raise DataValidationError(f"Destino de mapeo no reconocido: {target}")
        mapping[source_norm] = target_norm
    return mapping


def _resolve_columns(columns: list[str], manual_mapping: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    normalized_original = {_normalize_name(col): col for col in columns}
    rename: dict[str, str] = {}
    mapped_columns: dict[str, str] = {}
    used_targets: set[str] = set()

    for normalized, original in normalized_original.items():
        target = manual_mapping.get(normalized)
        if target is None:
            for canonical, aliases in COLUMN_ALIASES.items():
                if normalized in aliases:
                    target = canonical
                    break
        if target is None:
            target = normalized

        if target in used_targets:
            raise DataValidationError(
                f"Más de una columna fue interpretada como '{target}'. Revise el CSV o use un mapeo explícito."
            )
        used_targets.add(target)
        rename[original] = target
        if original != target:
            mapped_columns[original] = target

    return rename, mapped_columns


def load_and_validate_csv(
    path: str | Path,
    mapping_path: str | Path | None = None,
) -> ValidationResult:
    csv_path = Path(path)
    if not csv_path.exists():
        raise DataValidationError(f"No existe el archivo CSV: {csv_path}")

    try:
        raw = pd.read_csv(csv_path, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as exc:
        raise DataValidationError(f"No fue posible leer el CSV: {exc}") from exc

    if raw.empty:
        raise DataValidationError("El CSV no contiene registros.")

    manual_mapping = _load_manual_mapping(mapping_path)
    rename, mapped_columns = _resolve_columns([str(c) for c in raw.columns], manual_mapping)
    raw = raw.rename(columns=rename)

    missing = REQUIRED_COLUMNS - set(raw.columns)
    if missing:
        raise DataValidationError(
            "Faltan columnas obligatorias: " + ", ".join(sorted(missing))
        )

    total_rows = len(raw)
    df = raw.copy()
    df["_source_row"] = range(2, len(df) + 2)

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in NUMERIC_COLUMNS & set(df.columns):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    invalid_reason = pd.Series("", index=df.index, dtype="object")

    def mark(mask: pd.Series, reason: str) -> None:
        nonlocal invalid_reason
        invalid_reason = invalid_reason.mask(mask & (invalid_reason == ""), reason)

    mark(df["timestamp"].isna(), "timestamp inválido")
    mark(df["link_id"].isna() | (df["link_id"].astype(str).str.strip() == ""), "link_id vacío")
    mark(df["rx_mbps"].isna() | (df["rx_mbps"] < 0), "rx_mbps inválido")
    mark(df["tx_mbps"].isna() | (df["tx_mbps"] < 0), "tx_mbps inválido")
    mark(df["capacity_mbps"].isna() | (df["capacity_mbps"] <= 0), "capacity_mbps debe ser > 0")

    if "packet_loss_pct" in df.columns:
        mark(
            df["packet_loss_pct"].notna()
            & ((df["packet_loss_pct"] < 0) | (df["packet_loss_pct"] > 100)),
            "packet_loss_pct fuera de rango",
        )
    if "availability_pct" in df.columns:
        mark(
            df["availability_pct"].notna()
            & ((df["availability_pct"] < 0) | (df["availability_pct"] > 100)),
            "availability_pct fuera de rango",
        )
    if "latency_ms" in df.columns:
        mark(df["latency_ms"].notna() & (df["latency_ms"] < 0), "latency_ms inválido")

    duplicate_mask = (
        df["timestamp"].notna()
        & df["link_id"].notna()
        & df.duplicated(subset=["timestamp", "link_id"], keep="first")
    )
    mark(duplicate_mask, "registro duplicado para enlace y timestamp")

    rejected = df.loc[invalid_reason != ""].copy()
    rejected["rejection_reason"] = invalid_reason.loc[invalid_reason != ""]

    clean = df.loc[invalid_reason == ""].copy()
    clean["link_id"] = clean["link_id"].astype(str).str.strip()
    clean = clean.sort_values(["link_id", "timestamp"]).reset_index(drop=True)

    if clean.empty:
        raise DataValidationError("No quedaron registros válidos después de la validación.")

    clean["rx_utilization"] = clean["rx_mbps"] / clean["capacity_mbps"]
    clean["tx_utilization"] = clean["tx_mbps"] / clean["capacity_mbps"]
    clean["traffic_mbps"] = clean[["rx_mbps", "tx_mbps"]].max(axis=1)
    clean["utilization"] = clean[["rx_utilization", "tx_utilization"]].max(axis=1)
    clean["bottleneck_direction"] = np.where(
        clean["rx_utilization"] >= clean["tx_utilization"], "RX", "TX"
    )
    clean["over_capacity"] = clean["utilization"] > 1.0
    clean["date"] = clean["timestamp"].dt.floor("D")
    clean["hour"] = clean["timestamp"].dt.hour

    warnings: list[str] = []
    over_count = int(clean["over_capacity"].sum())
    if over_count:
        warnings.append(
            f"{over_count} mediciones superan la capacidad nominal informada; valide unidades, shaping o capacidad configurada."
        )
    if mapped_columns:
        warnings.append(f"Se mapearon automáticamente {len(mapped_columns)} columnas.")

    return ValidationResult(
        data=clean,
        rejected=rejected,
        total_rows=total_rows,
        accepted_rows=len(clean),
        rejected_rows=len(rejected),
        mapped_columns=mapped_columns,
        warnings=tuple(warnings),
    )
