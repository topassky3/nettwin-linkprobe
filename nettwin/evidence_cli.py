from __future__ import annotations

import argparse
import sys

from .evidence_engine import EvidenceConfig, analyze_evidence, write_evidence


def build_evidence_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin analyze-evidence",
        description=(
            "Convertir eventos y correlaciones en hallazgos trazables: "
            "hecho, interpretación, hipótesis, confianza y recomendación."
        ),
    )
    parser.add_argument("--events-json", required=True, help="events.json generado por Event Engine")
    parser.add_argument(
        "--correlations-json",
        help="correlations.json generado por Correlation Engine; opcional pero recomendado",
    )
    parser.add_argument("--output", "-o", default="resultados_evidence", help="Directorio de salida")
    parser.add_argument(
        "--min-support-rho",
        type=float,
        default=0.60,
        help="|rho| mínimo para usar una correlación suficiente como soporte fuerte",
    )
    parser.add_argument(
        "--max-correlation-refs",
        type=int,
        default=8,
        help="Máximo de referencias correlacionales por categoría y hallazgo",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_evidence_parser()
    args = parser.parse_args(argv)
    try:
        config = EvidenceConfig(
            min_support_abs_rho=args.min_support_rho,
            max_correlation_refs=args.max_correlation_refs,
        )
        result = analyze_evidence(
            events_json=args.events_json,
            correlations_json=args.correlations_json,
            config=config,
        )
        path = write_evidence(result, args.output)
        print("Evidence Engine completado correctamente.")
        print("Alcance: únicamente ventana observada")
        print(f"Eventos de entrada: {result.metadata['events_source']['event_count']}")
        if result.metadata["correlations_source"] is None:
            print("Correlaciones: N/D (no se suministró correlations.json)")
        else:
            print(
                "Correlaciones de entrada: "
                f"{result.metadata['correlations_source']['correlation_count']}"
            )
        print(f"Hallazgos generados: {len(result.findings)}")
        for finding in result.findings:
            refs = len(finding["evidence"]["supporting_correlations"])
            print(
                f"  {finding['finding_id']} <- {finding['event_id']} | "
                f"{finding['confidence']} | soporte fuerte={refs} | {finding['severity']}"
            )
            print(f"    HECHO: {finding['fact']}")
            print(f"    HIPÓTESIS: {finding['hypothesis']}")
            print(f"    RECOMENDACIÓN: {finding['recommendation']}")
        print("Causalidad: NO inferida por Evidence Engine")
        print(f"Evidencia: {path.resolve()}")
        return 0
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR INESPERADO: {exc}", file=sys.stderr)
        return 3
