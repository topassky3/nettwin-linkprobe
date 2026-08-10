from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .fingerprint_engine import (
    FingerprintConfig,
    analyze_fingerprints,
    write_fingerprints,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin analyze-fingerprints",
        description=(
            "Crear fingerprints compactos por evento sin inventar baselines "
            "ni inferir causalidad."
        ),
    )
    parser.add_argument(
        "--events-json",
        required=True,
        help="events.json generado por Event Engine",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="resultados_fingerprints",
        help="Directorio de salida",
    )
    parser.add_argument(
        "--round-digits",
        type=int,
        default=3,
        help="Decimales usados en el vector compacto",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = analyze_fingerprints(
            args.events_json,
            config=FingerprintConfig(round_digits=args.round_digits),
        )
        path = write_fingerprints(result, Path(args.output))
        print("Fingerprint Engine completado correctamente.")
        print("Alcance: únicamente ventana observada")
        print(f"Versión de vector: {result.metadata['vector_version']}")
        print(f"Eventos de entrada: {result.metadata['events_source']['event_count']}")
        print(f"Fingerprints generados: {len(result.fingerprints)}")

        def text(value, suffix=""):
            return "N/D" if value is None else f"{value}{suffix}"

        for fingerprint in result.fingerprints:
            vector = fingerprint["compact_vector"]
            print(
                f"  {fingerprint['fingerprint_id']} <- {fingerprint['event_id']} | "
                f"Δutil={text(vector['delta_utilization_pp'], ' pp')} | "
                f"ΔRTT={text(vector['delta_rtt_pct'], '%')} | "
                f"Δdelay={text(vector['delta_delay_variation_pct'], '%')} | "
                f"Δloss={text(vector['delta_loss_pp'], ' pp')} | "
                f"Δdrops={text(vector['delta_drops'])} | "
                f"{text(vector['duration_seconds'], ' s')}"
            )
            for limitation in fingerprint["limitations"]:
                print(f"    LIMITACIÓN: {limitation}")
        print("Causalidad: NO inferida por Fingerprint Engine")
        print(f"Fingerprints: {path.resolve()}")
        return 0
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR INESPERADO: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
