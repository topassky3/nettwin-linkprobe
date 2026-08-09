from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_payload() -> dict:
    return {
        "metadata": {
            "mode": "event-engine",
            "scope": "observed_window_only",
            "event_count": 2,
            "limitations": [
                "Datos sintéticos exclusivos para depuración de Fingerprint Engine."
            ],
        },
        "events": [
            {
                "event_id": "EVENT-003",
                "start": "2026-08-09T17:14:22+00:00",
                "end": "2026-08-09T17:21:48+00:00",
                "duration_seconds": 446.0,
                "severity": "high",
                "confidence": "ALTA",
                "metrics_changed": [
                    "utilization",
                    "rtt",
                    "delay_variation",
                    "packet_loss",
                    "drops",
                ],
                "evidence": {
                    "bucket_seconds": 5,
                    "anomalous_buckets": 2,
                    "buckets": [
                        {
                            "timestamp": "2026-08-09T17:14:22+00:00",
                            "metrics_changed": [
                                "utilization",
                                "rtt",
                                "delay_variation",
                                "packet_loss",
                                "drops",
                            ],
                            "detail": {
                                "utilization": {
                                    "value_pct": 91.0,
                                    "baseline_pct": 58.0,
                                },
                                "rtt": [
                                    {
                                        "target": "external",
                                        "value_ms": 49.0,
                                        "baseline_ms": 14.0,
                                    }
                                ],
                                "delay_variation": [
                                    {
                                        "target": "external",
                                        "value_ms": 15.0,
                                        "baseline_ms": 2.0,
                                    }
                                ],
                                "packet_loss": [
                                    {
                                        "target": "external",
                                        "loss_pct": 1.3,
                                        "baseline_pct": 0.0,
                                        "reachability": True,
                                    }
                                ],
                                "drops": {"delta": 20.0},
                            },
                        },
                        {
                            "timestamp": "2026-08-09T17:21:43+00:00",
                            "metrics_changed": ["drops"],
                            "detail": {"drops": {"delta": 27.0}},
                        },
                    ],
                },
            },
            {
                "event_id": "EVENT-004",
                "start": "2026-08-09T18:00:00+00:00",
                "end": "2026-08-09T18:00:20+00:00",
                "duration_seconds": 20.0,
                "severity": "medium",
                "confidence": "MEDIA",
                "metrics_changed": ["rtt", "packet_loss", "drops"],
                "evidence": {
                    "bucket_seconds": 5,
                    "anomalous_buckets": 2,
                    "buckets": [
                        {
                            "timestamp": "2026-08-09T18:00:00+00:00",
                            "metrics_changed": ["rtt", "packet_loss"],
                            "detail": {
                                "rtt": [
                                    {
                                        "target": "external",
                                        "value_ms": 20.0,
                                        "baseline_ms": 10.0,
                                    }
                                ],
                                "packet_loss": [
                                    {
                                        "target": "external",
                                        "loss_pct": 2.5,
                                        "reachability": True,
                                    }
                                ],
                            },
                        },
                        {
                            "timestamp": "2026-08-09T18:00:05+00:00",
                            "metrics_changed": ["drops"],
                            "detail": {"drops": {"delta": 4.0}},
                        },
                    ],
                },
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="debug_fase9")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "events.json"
    path.write_text(
        json.dumps(build_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Datos de depuración de Fase 9 generados.")
    print(f"Eventos: {path.resolve()}")
    print(
        "EVENT-003 esperado: +33 pp util, +250% RTT, +650% delay, "
        "+1.3 pp loss, +47 drops, 446 s."
    )
    print(
        "EVENT-004 esperado: Δloss=N/D por falta de baseline; "
        "pico loss observado=2.5%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
