from __future__ import annotations

import argparse
import json
from pathlib import Path


def _correlation(relation_id, x_metric, y_metric, *, rho, target="external", status="descriptive_association"):
    return {
        "relation_id": relation_id,
        "x_metric": x_metric,
        "y_metric": y_metric,
        "target": target,
        "pair_count": 48,
        "spearman_rho": rho,
        "abs_rho": abs(rho),
        "direction": "positive",
        "strength": "very_strong" if abs(rho) >= 0.80 else ("strong" if abs(rho) >= 0.60 else "very_weak"),
        "sufficient_samples": True,
        "evidence_status": status,
        "descriptive_significance": "adequate_descriptive" if status == "descriptive_association" else "low",
        "first_timestamp": "2026-08-09T19:00:00+00:00",
        "last_timestamp": "2026-08-09T19:04:00+00:00",
        "causal_interpretation_allowed": False,
        "note": "Asociación descriptiva durante la ventana observada; no implica causalidad.",
    }


def generate(output: str | Path) -> tuple[Path, Path]:
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)

    event = {
        "event_id": "EVENT-001",
        "start": "2026-08-09T19:01:00+00:00",
        "end": "2026-08-09T19:01:15+00:00",
        "duration_seconds": 15.0,
        "metrics_changed": ["delay_variation", "drops", "packet_loss", "rtt", "utilization"],
        "severity": "high",
        "confidence": "ALTA",
        "confidence_scope": "detección_del_evento_no_causalidad",
        "evidence": {
            "bucket_seconds": 5,
            "anomalous_buckets": 3,
            "buckets": [
                {
                    "timestamp": "2026-08-09T19:01:00+00:00",
                    "metrics_changed": ["delay_variation", "rtt", "utilization"],
                    "detail": {
                        "utilization": {"value_pct": 88.0, "baseline_pct": 30.0},
                        "rtt": [{"target": "external", "value_ms": 45.0, "baseline_ms": 15.0}],
                        "delay_variation": [{"target": "external", "value_ms": 18.0, "baseline_ms": 1.0}],
                    },
                },
                {
                    "timestamp": "2026-08-09T19:01:05+00:00",
                    "metrics_changed": ["delay_variation", "drops", "rtt", "utilization"],
                    "detail": {
                        "utilization": {"value_pct": 92.0, "baseline_pct": 30.0},
                        "drops": {"delta": 3.0},
                        "rtt": [{"target": "external", "value_ms": 55.0, "baseline_ms": 15.0}],
                        "delay_variation": [{"target": "external", "value_ms": 22.0, "baseline_ms": 1.0}],
                    },
                },
                {
                    "timestamp": "2026-08-09T19:01:10+00:00",
                    "metrics_changed": ["packet_loss", "utilization"],
                    "detail": {
                        "utilization": {"value_pct": 86.0, "baseline_pct": 30.0},
                        "packet_loss": [{"target": "external", "loss_pct": 5.0, "reachability": True}],
                    },
                },
            ],
        },
        "interpretation": "Se observaron cambios simultáneos durante la ventana medida.",
        "hypothesis": "Requiere evidencia adicional.",
    }

    correlations = [
        _correlation("utilization__rtt__external", "utilization_pct", "rtt_ms", rho=0.94),
        _correlation("utilization__delay_variation__external", "utilization_pct", "delay_variation_ms", rho=0.89),
        _correlation("utilization__packet_loss__external", "utilization_pct", "packet_loss_pct", rho=0.82),
        _correlation("utilization__drops", "utilization_pct", "drops_delta", rho=0.78, target=None),
        _correlation("cpu__rtt__external", "cpu_percent", "rtt_ms", rho=0.05, status="weak_association"),
        _correlation("cpu__packet_loss__external", "cpu_percent", "packet_loss_pct", rho=0.03, status="weak_association"),
        _correlation("utilization__rtt__internal", "utilization_pct", "rtt_ms", rho=0.99, target="internal"),
    ]

    events_path = root / "events.json"
    correlations_path = root / "correlations.json"
    events_path.write_text(
        json.dumps(
            {
                "metadata": {"mode": "event-engine", "scope": "observed_window_only", "event_count": 1},
                "events": [event],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    correlations_path.write_text(
        json.dumps(
            {
                "metadata": {"mode": "correlation-engine", "scope": "observed_window_only", "relation_count": len(correlations)},
                "correlations": correlations,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return events_path, correlations_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generar entradas sintéticas reproducibles para Fase 8")
    parser.add_argument("--output", default="debug_fase8")
    args = parser.parse_args()
    events, correlations = generate(args.output)
    print("Datos de depuración de Fase 8 generados.")
    print(f"Eventos: {events.resolve()}")
    print(f"Correlaciones: {correlations.resolve()}")
    print("Esperado: FINDING-001 | ALTA | 4 correlaciones fuertes de soporte.")
    print("Hipótesis esperada: compatible con formación de colas o cuello de botella bajo carga.")
    print("CPU: únicamente contexto débil, no soporte causal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
