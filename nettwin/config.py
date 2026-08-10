from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalysisConfig:
    target_utilization: float = 0.70
    warning_threshold: float = 0.75
    critical_threshold: float = 0.85
    warning_days: int = 90
    critical_days: int = 30

    minimum_trend_days: int = 7
    minimum_trend_r2: float = 0.35
    minimum_growth_pp_per_day: float = 0.05

    latency_threshold_ms: float = 80.0
    packet_loss_threshold_pct: float = 2.0
    availability_threshold_pct: float = 99.0
    quality_correlation_threshold: float = 0.35
    latency_high_load_delta_ms: float = 5.0
    loss_high_load_delta_pct: float = 0.30
    high_load_quantile: float = 0.75

    def validate(self) -> None:
        for name in (
            "target_utilization",
            "warning_threshold",
            "critical_threshold",
            "minimum_trend_r2",
            "quality_correlation_threshold",
            "high_load_quantile",
        ):
            value = getattr(self, name)
            if not 0 < value <= 1:
                raise ValueError(f"{name} debe estar entre 0 y 1.")

        if self.warning_threshold >= self.critical_threshold:
            raise ValueError("warning_threshold debe ser menor que critical_threshold.")
        if self.target_utilization >= self.critical_threshold:
            raise ValueError("target_utilization debe ser menor que critical_threshold.")
        if self.critical_days <= 0 or self.warning_days <= 0:
            raise ValueError("Los horizontes de días deben ser positivos.")
        if self.critical_days >= self.warning_days:
            raise ValueError("critical_days debe ser menor que warning_days.")
        if self.minimum_trend_days < 3:
            raise ValueError("minimum_trend_days debe ser al menos 3.")
        if self.minimum_growth_pp_per_day < 0:
            raise ValueError("minimum_growth_pp_per_day no puede ser negativo.")
        if self.latency_threshold_ms <= 0:
            raise ValueError("latency_threshold_ms debe ser positivo.")
        if not 0 <= self.packet_loss_threshold_pct <= 100:
            raise ValueError("packet_loss_threshold_pct debe estar entre 0 y 100.")
        if not 0 <= self.availability_threshold_pct <= 100:
            raise ValueError("availability_threshold_pct debe estar entre 0 y 100.")
        if self.latency_high_load_delta_ms < 0 or self.loss_high_load_delta_pct < 0:
            raise ValueError("Los deltas de calidad no pueden ser negativos.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: str | Path | None) -> AnalysisConfig:
    if path is None:
        config = AnalysisConfig()
        config.validate()
        return config

    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"No existe el archivo de configuración: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido en {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("La configuración debe ser un objeto JSON.")

    allowed = set(AnalysisConfig.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Parámetros desconocidos en configuración: {sorted(unknown)}")

    config = AnalysisConfig(**raw)
    config.validate()
    return config
