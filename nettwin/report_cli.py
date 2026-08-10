from __future__ import annotations

import argparse
import json
from pathlib import Path

from nettwin.report_engine import ReportConfig, ReportResult, generate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin report",
        description=(
            "Generar Link Health Audit reproducible desde un run sellado. "
            "Verifica integridad, evalúa calidad del dataset y ejecuta el pipeline analítico antes del informe."
        ),
    )
    parser.add_argument("--run", required=True, help="Directorio del run sellado")
    parser.add_argument(
        "--output",
        help="Directorio externo para report.json/report.html y artefactos analíticos; por defecto reports/<run_id>",
    )
    parser.add_argument("--company", default="HacheNet", help="Nombre mostrado en la portada")
    parser.add_argument("--link", default="LINK-01", help="Identificador legible del enlace")
    parser.add_argument(
        "--capacity-mbps",
        type=float,
        help="Capacidad explícita/autorizada del enlace. Si se omite, utilización queda N/D salvo que exista en config.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Intentar generar report.pdf con WeasyPrint si está disponible; HTML siempre se genera.",
    )
    return parser


def write_analysis_manifest(result: ReportResult) -> Path:
    report = json.loads(result.report_json.read_text(encoding="utf-8"))
    payload = {
        "schema_version": "linkprobe-analysis-v1",
        "run_id": report.get("run_id"),
        "analysis_executed": bool(report.get("analysis_executed")),
        "safe_for_conclusions": bool(report.get("dataset_quality", {}).get("safe_for_conclusions")),
        "quality_status": report.get("dataset_quality", {}).get("status"),
        "capacity_mbps": report.get("capacity_mbps"),
        "primary_target": report.get("primary_target"),
        "event_count": len(report.get("events", [])),
        "finding_count": len(report.get("findings", [])),
        "fingerprint_count": len(report.get("fingerprints", [])),
        "headline_metrics": report.get("headline_metrics", {}),
        "analysis_artifacts": report.get("analysis_artifacts", {}),
        "source_sha256": report.get("source_sha256", {}),
        "report_sha256": report.get("report_sha256"),
        "limitations": report.get("limitations", []),
        "causal_inference_performed": False,
    }
    path = result.output_dir / "analysis.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = generate_report(
            Path(args.run),
            Path(args.output) if args.output else None,
            config=ReportConfig(
                company=args.company,
                link_name=args.link,
                capacity_mbps=args.capacity_mbps,
                render_pdf=bool(args.pdf),
            ),
        )
        analysis_manifest = write_analysis_manifest(result)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print("Informe técnico generado.")
    print(f"Run: {result.run_dir}")
    print(f"Integridad estricta: {'VÁLIDA' if result.integrity_valid else 'INVÁLIDA'}")
    print(f"Quality Gate: {result.quality_status}")
    print(f"Conclusiones habilitadas: {'SÍ' if result.safe_for_conclusions else 'NO'}")
    print(f"Pipeline analítico ejecutado: {'SÍ' if result.analysis_executed else 'NO'}")
    print(f"Analysis: {analysis_manifest}")
    print(f"JSON: {result.report_json}")
    print(f"HTML: {result.report_html}")
    if args.pdf:
        if result.report_pdf is not None:
            print(f"PDF: {result.report_pdf}")
        else:
            print(result.pdf_error or "PDF no generado.")
    if not result.integrity_valid or not result.safe_for_conclusions:
        return 2
    if args.pdf and result.report_pdf is None:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
