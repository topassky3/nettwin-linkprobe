from __future__ import annotations

from html import escape
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .analytics import AnalysisResult
from .config import AnalysisConfig
from .io import ValidationResult


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, (float, np.floating)) and (math.isnan(float(value)) or math.isinf(float(value))))


def _fmt(value: object, decimals: int = 1, suffix: str = "") -> str:
    if _is_missing(value):
        return "N/D"
    return f"{float(value):.{decimals}f}{suffix}"


def _status_class(status: str) -> str:
    return {
        "CRÍTICO": "critical",
        "ALTO": "high",
        "MEDIO": "medium",
        "NORMAL": "normal",
    }.get(status, "normal")


def _confidence_class(confidence: str) -> str:
    return {
        "ALTA": "normal",
        "MEDIA": "medium",
        "BAJA": "high",
        "INSUFICIENTE": "muted",
    }.get(confidence, "muted")


def save_charts(result: AnalysisResult, output_dir: Path, config: AnalysisConfig) -> dict[str, str]:
    paths: dict[str, str] = {}
    summary = result.summary.copy()
    x = np.arange(len(summary))

    fig, ax = plt.subplots(figsize=(11, 5.6))
    width = 0.34
    ax.bar(x - width / 2, summary["rx_p95_utilization_pct"], width, label="RX P95")
    ax.bar(x + width / 2, summary["tx_p95_utilization_pct"], width, label="TX P95")
    ax.axhline(config.warning_threshold * 100, linestyle="--", linewidth=1, label="Alerta")
    ax.axhline(config.critical_threshold * 100, linestyle="--", linewidth=1, label="Crítico")
    ax.set_xticks(x, summary["link_id"], rotation=18, ha="right")
    ax.set_ylabel("Utilización (%)")
    ax.set_title("Utilización P95 por dirección")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = output_dir / "utilizacion_rx_tx.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths["utilization"] = path.name

    fig, ax = plt.subplots(figsize=(11, 5.6))
    for link_id, group in result.hourly.groupby("link_id"):
        ax.plot(group["hour"], group["p95_utilization"] * 100, marker="o", label=link_id)
    ax.axhline(config.critical_threshold * 100, linestyle="--", linewidth=1, label="Umbral crítico")
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("Hora del día")
    ax.set_ylabel("Utilización dominante P95 (%)")
    ax.set_title("Perfil horario de utilización")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    path = output_dir / "horas_criticas.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths["hourly"] = path.name

    fig, ax = plt.subplots(figsize=(11, 5.6))
    for link_id, group in result.daily.groupby("link_id"):
        group = group.sort_values("date")
        ax.plot(group["date"], group["daily_p95_utilization"] * 100, marker=".", label=link_id)
        summary_row = result.summary.loc[result.summary["link_id"] == link_id].iloc[0]
        if bool(summary_row["trend_reliable"]):
            x_days = (group["date"] - group["date"].min()).dt.days.to_numpy(dtype=float)
            slope = float(summary_row["trend_pct_points_per_day"])
            intercept = float((group["daily_p95_utilization"] * 100 - slope * x_days).mean())
            ax.plot(group["date"], intercept + slope * x_days, linestyle="--", linewidth=1)
    ax.axhline(config.critical_threshold * 100, linestyle="--", linewidth=1, label="Umbral crítico")
    ax.set_xlabel("Fecha")
    ax.set_ylabel("P95 diario (%)")
    ax.set_title("Tendencia diaria; línea discontinua solo cuando la proyección es confiable")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = output_dir / "tendencia_confiable.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths["trend"] = path.name

    fig, ax = plt.subplots(figsize=(11, 5.6))
    width = 0.25
    ax.bar(x - width, summary["capacity_mbps"], width, label="Actual")
    ax.bar(x, summary["technical_required_capacity_mbps"], width, label="Mínimo técnico estimado")
    ax.bar(x + width, summary["recommended_capacity_mbps"], width, label="Siguiente capacidad comercial")
    ax.set_xticks(x, summary["link_id"], rotation=18, ha="right")
    ax.set_ylabel("Mbps")
    ax.set_title("Escenario de capacidad")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = output_dir / "capacidad_recomendada.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths["capacity"] = path.name

    if "latency_ms" in result.processed.columns and result.processed["latency_ms"].notna().any():
        fig, ax = plt.subplots(figsize=(11, 5.6))
        for link_id, group in result.processed.groupby("link_id"):
            sampled = group.dropna(subset=["latency_ms"]).iloc[::4]
            ax.scatter(sampled["utilization"] * 100, sampled["latency_ms"], s=12, alpha=0.45, label=link_id)
        ax.set_xlabel("Utilización dominante (%)")
        ax.set_ylabel("Latencia (ms)")
        ax.set_title("Asociación exploratoria entre carga y latencia")
        ax.legend()
        ax.grid(alpha=0.25)
        fig.tight_layout()
        path = output_dir / "latencia_vs_carga.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths["latency"] = path.name

    return paths


def _days_text(row: pd.Series) -> str:
    days = row["days_to_critical"]
    if _is_missing(days):
        return "No concluyente"
    if float(days) == 0:
        return "Umbral superado"
    return f"{float(days):.0f} días"


def _summary_rows_html(summary: pd.DataFrame) -> str:
    rows: list[str] = []
    for _, row in summary.iterrows():
        trend = row["trend_pct_points_per_day"]
        trend_text = "N/D" if _is_missing(trend) else f"{float(trend):+.2f} pp/día"
        confidence = escape(str(row["trend_confidence"]))
        rows.append(
            "<tr>"
            f"<td><strong>{escape(str(row['link_id']))}</strong><div class='small'>{escape(str(row['recommended_action']))}</div></td>"
            f"<td><span class='badge {_status_class(str(row['status']))}'>{escape(str(row['status']))}</span></td>"
            f"<td>{row['capacity_mbps']:.0f}</td>"
            f"<td><strong>{row['p95_utilization_pct']:.1f}%</strong><div class='small'>RX {row['rx_p95_utilization_pct']:.1f}% · TX {row['tx_p95_utilization_pct']:.1f}%</div></td>"
            f"<td>{escape(str(row['dominant_direction']))}</td>"
            f"<td>{int(row['peak_hour']):02d}:00–{(int(row['peak_hour']) + 1) % 24:02d}:00</td>"
            f"<td>{trend_text}<div class='small'>R² {_fmt(row['trend_r2'], 2)} · <span class='badge {_confidence_class(str(row['trend_confidence']))}'>{confidence}</span></div></td>"
            f"<td>{_days_text(row)}</td>"
            f"<td>{row['technical_required_capacity_mbps']:.1f}<div class='small'>comercial: {row['recommended_capacity_mbps']:.0f}</div></td>"
            f"<td>{escape(str(row['quality_load_association']))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _alerts_html(alerts: pd.DataFrame) -> str:
    if alerts.empty:
        return "<p class='ok'>No se detectaron alertas con los umbrales configurados.</p>"
    items: list[str] = []
    for _, row in alerts.iterrows():
        items.append(
            f"<li><strong>{escape(str(row['severity']))} · {escape(str(row['link_id']))} · "
            f"{escape(str(row['type']))}:</strong> {escape(str(row['message']))}</li>"
        )
    return "<ul class='alerts'>" + "".join(items) + "</ul>"


def _warnings_html(validation: ValidationResult) -> str:
    if not validation.warnings:
        return ""
    items = "".join(f"<li>{escape(w)}</li>" for w in validation.warnings)
    return f"<div class='warning'><strong>Advertencias de datos</strong><ul>{items}</ul></div>"


def _write_executive_summary(
    result: AnalysisResult, validation: ValidationResult, output: Path, company: str, demo_mode: bool
) -> None:
    lines = [
        f"# Resumen ejecutivo — NetTwin ISP para {company}",
        "",
        ("> Demostración con datos sintéticos. No representa la operación real de la empresa." if demo_mode else "> Diagnóstico basado en el archivo suministrado. Las proyecciones requieren validación técnica."),
        "",
        f"- Enlaces analizados: **{len(result.summary)}**",
        f"- Mediciones aceptadas: **{validation.accepted_rows}**",
        f"- Mediciones rechazadas: **{validation.rejected_rows}**",
        "",
        "## Prioridades",
        "",
    ]
    for _, row in result.summary.iterrows():
        lines.append(
            f"- **{row['link_id']} — {row['status']}**: P95 {row['p95_utilization_pct']:.1f}% "
            f"({row['dominant_direction']}); mínimo técnico estimado {row['technical_required_capacity_mbps']:.1f} Mbps; "
            f"acción: {row['recommended_action']}"
        )
    lines.extend([
        "",
        "## Próximo paso propuesto",
        "",
        "Recibir una exportación anónima de uno o dos enlaces, documentar las unidades y la capacidad real, adaptar el importador y entregar un diagnóstico validado con el equipo técnico.",
    ])
    output.write_text("\n".join(lines), encoding="utf-8")


def generate_report(
    result: AnalysisResult,
    validation: ValidationResult,
    config: AnalysisConfig,
    output_dir: str | Path,
    company: str,
    source_file: str,
    demo_mode: bool = False,
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    result.summary.to_csv(out / "resumen_enlaces.csv", index=False, encoding="utf-8-sig")
    result.alerts.to_csv(out / "alertas.csv", index=False, encoding="utf-8-sig")
    result.daily.to_csv(out / "metricas_diarias.csv", index=False, encoding="utf-8-sig")
    result.hourly.to_csv(out / "metricas_horarias.csv", index=False, encoding="utf-8-sig")
    result.processed.to_csv(out / "datos_procesados.csv", index=False, encoding="utf-8-sig")
    validation.rejected.to_csv(out / "filas_rechazadas.csv", index=False, encoding="utf-8-sig")
    result.summary.to_json(out / "diagnostico.json", orient="records", force_ascii=False, indent=2, date_format="iso")
    (out / "configuracion_usada.json").write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_executive_summary(result, validation, out / "resumen_ejecutivo.md", company, demo_mode)

    charts = save_charts(result, out, config)
    counts = result.summary["status"].value_counts().to_dict()
    total_links = len(result.summary)
    generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    demo_banner = (
        "<div class='demo'><strong>DEMOSTRACIÓN:</strong> los datos son sintéticos y no representan la operación real de " + escape(company) + ".</div>"
        if demo_mode else ""
    )
    chart_latency = (
        f'<div class="chart"><img src="{charts["latency"]}" alt="Latencia frente a carga"><p>Asociación exploratoria; no demuestra causalidad.</p></div>'
        if "latency" in charts else ""
    )

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NetTwin ISP — Informe {escape(company)}</title>
<style>
:root {{ --bg:#f5f7fb; --card:#fff; --text:#172033; --muted:#667085; --line:#e4e7ec; --hero:#0b1736; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Inter,Arial,Helvetica,sans-serif; background:var(--bg); color:var(--text); line-height:1.45; }}
.container {{ max-width:1240px; margin:0 auto; padding:28px 20px 64px; }}
.hero {{ background:linear-gradient(135deg,#0b1736,#1f3b73); color:white; border-radius:20px; padding:32px; margin-bottom:18px; }}
.hero h1 {{ margin:0 0 6px; font-size:34px; }} .hero p {{ margin:5px 0; color:#d6e0f5; }}
.grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin:18px 0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:15px; padding:18px; box-shadow:0 5px 18px rgba(16,24,40,.05); }}
.metric {{ font-size:29px; font-weight:750; margin-top:5px; }} .label,.small {{ color:var(--muted); font-size:12px; }}
.small {{ margin-top:4px; max-width:330px; }} section {{ margin-top:24px; }} h2 {{ margin-bottom:10px; }}
table {{ width:100%; border-collapse:collapse; background:white; border-radius:14px; overflow:hidden; font-size:13px; }}
th,td {{ border-bottom:1px solid var(--line); padding:12px 9px; text-align:left; vertical-align:top; }}
th {{ background:#f9fafb; color:#475467; font-size:11px; text-transform:uppercase; letter-spacing:.03em; }}
.badge {{ display:inline-block; padding:4px 8px; border-radius:999px; color:white; font-size:10px; font-weight:750; }}
.critical {{ background:#b42318; }} .high {{ background:#b54708; }} .medium {{ background:#b28a00; }} .normal {{ background:#027a48; }} .muted {{ background:#667085; }}
.chart {{ width:100%; background:white; padding:14px; border-radius:15px; border:1px solid var(--line); margin:13px 0; }}
.chart img {{ width:100%; height:auto; display:block; }} .chart p {{ color:var(--muted); font-size:12px; margin:7px 0 0; }}
.alerts {{ background:white; border:1px solid var(--line); border-radius:14px; padding:18px 18px 18px 38px; }} .alerts li {{ margin:9px 0; }}
.demo {{ background:#101828; color:white; border-radius:13px; padding:15px; margin-bottom:12px; }}
.note {{ background:#eef4ff; border:1px solid #b2ccff; border-radius:13px; padding:15px; color:#194185; }}
.warning {{ background:#fffaeb; border:1px solid #fedf89; border-radius:13px; padding:14px; color:#7a2e0e; margin-top:12px; }}
.ok {{ background:#ecfdf3; border:1px solid #abefc6; padding:14px; border-radius:12px; }}
.footer {{ color:var(--muted); margin-top:32px; font-size:12px; }}
@media(max-width:980px) {{ .grid {{ grid-template-columns:repeat(2,1fr); }} .table-wrap {{ overflow-x:auto; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <h1>NetTwin ISP <span style="font-size:15px;opacity:.75">v0.2.2</span></h1>
    <p>Diagnóstico de capacidad y calidad de red para <strong>{escape(company)}</strong></p>
    <p>Fuente: {escape(source_file)} · Generado: {generated_at}</p>
  </div>

  {demo_banner}
  <div class="note"><strong>Alcance:</strong> herramienta de apoyo a la planificación. Las fechas proyectadas solo se muestran cuando existe evidencia estadística mínima. La asociación entre carga y calidad no prueba causalidad.</div>
  {_warnings_html(validation)}

  <div class="grid">
    <div class="card"><div class="label">Enlaces analizados</div><div class="metric">{total_links}</div></div>
    <div class="card"><div class="label">Críticos</div><div class="metric">{counts.get('CRÍTICO', 0)}</div></div>
    <div class="card"><div class="label">Riesgo alto</div><div class="metric">{counts.get('ALTO', 0)}</div></div>
    <div class="card"><div class="label">Registros aceptados</div><div class="metric">{validation.accepted_rows}</div></div>
    <div class="card"><div class="label">Registros rechazados</div><div class="metric">{validation.rejected_rows}</div></div>
  </div>

  <section>
    <h2>Resumen ejecutivo por enlace</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Enlace y acción</th><th>Estado</th><th>Actual Mbps</th><th>Utilización</th><th>Cuello</th><th>Hora crítica</th><th>Tendencia</th><th>Hasta 85%</th><th>Mínimo / comercial</th><th>Carga-calidad</th></tr></thead>
      <tbody>{_summary_rows_html(result.summary)}</tbody>
    </table></div>
  </section>

  <section><h2>Alertas priorizadas</h2>{_alerts_html(result.alerts)}</section>

  <section><h2>Evidencia visual</h2>
    <div class="chart"><img src="{charts['utilization']}" alt="Utilización RX y TX"></div>
    <div class="chart"><img src="{charts['hourly']}" alt="Horas críticas"></div>
    <div class="chart"><img src="{charts['trend']}" alt="Tendencia confiable"></div>
    <div class="chart"><img src="{charts['capacity']}" alt="Capacidad recomendada"><p>La capacidad comercial es una referencia configurable, no una orden de compra.</p></div>
    {chart_latency}
  </section>

  <section><h2>Parámetros utilizados</h2><div class="card">
    <p>Objetivo operativo: <strong>{config.target_utilization * 100:.0f}%</strong> · Alerta: <strong>{config.warning_threshold * 100:.0f}%</strong> · Crítico: <strong>{config.critical_threshold * 100:.0f}%</strong></p>
    <p>Tendencia: mínimo <strong>{config.minimum_trend_days} días</strong>, R² mínimo <strong>{config.minimum_trend_r2:.2f}</strong>, crecimiento mínimo <strong>{config.minimum_growth_pp_per_day:.2f} pp/día</strong>.</p>
    <p>Calidad: latencia P95 <strong>{config.latency_threshold_ms:.0f} ms</strong>, pérdida P95 <strong>{config.packet_loss_threshold_pct:.1f}%</strong>, disponibilidad <strong>{config.availability_threshold_pct:.1f}%</strong>.</p>
  </div></section>

  <div class="footer">NetTwin ISP v0.2.2 · MVP local y reproducible · Sin acceso directo a routers ni datos personales.</div>
</div>
</body>
</html>"""

    report_path = out / "informe_hachenet.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
