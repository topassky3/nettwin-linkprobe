from __future__ import annotations

from datetime import datetime
from html import escape
import math
from typing import Any


ND = "N/D"
_PALETTE = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return ND
    return f"{number:.{digits}f}{suffix}"


def _dt(value: Any) -> datetime | None:
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


def _clock(value: Any) -> str:
    parsed = _dt(value)
    if parsed is None:
        return ND
    return parsed.strftime("%Y-%m-%d %H:%M:%S %z")


def _duration(seconds: Any) -> str:
    value = _number(seconds)
    if value is None:
        return ND
    if value >= 3600:
        return f"{value / 3600:.2f} h"
    if value >= 60:
        return f"{value / 60:.2f} min"
    return f"{value:.1f} s"


def svg_chart(
    title: str,
    series: list[dict[str, Any]],
    *,
    global_start: datetime | None,
    global_end: datetime | None,
    unit: str = "",
    height: int = 190,
) -> str:
    width = 920
    left, right, top, bottom = 58, 18, 28, 34
    plot_w = width - left - right
    plot_h = height - top - bottom
    prepared: list[tuple[str, list[tuple[float, float]]]] = []
    values: list[float] = []
    timestamps: list[datetime] = []
    for item in series:
        points: list[tuple[float, float]] = []
        for raw_ts, raw_value in item.get("points", []):
            ts = _dt(raw_ts)
            value = _number(raw_value)
            if ts is None or value is None:
                continue
            epoch = ts.timestamp()
            points.append((epoch, value))
            values.append(value)
            timestamps.append(ts)
        if points:
            prepared.append((str(item.get("label", "serie")), points))
    if not prepared:
        return (
            '<div class="chart-card">'
            f'<div class="chart-title">{escape(title)}</div>'
            '<div class="chart-nd">N/D — métrica no disponible en esta ejecución.</div>'
            '</div>'
        )

    start = global_start or min(timestamps)
    end = global_end or max(timestamps)
    x0, x1 = start.timestamp(), end.timestamp()
    if x1 <= x0:
        x1 = x0 + 1.0
    y0, y1 = min(values), max(values)
    if y1 <= y0:
        pad = max(1.0, abs(y0) * 0.1)
        y0 -= pad
        y1 += pad
    else:
        pad = (y1 - y0) * 0.08
        y0 -= pad
        y1 += pad

    def sx(x: float) -> float:
        return left + (x - x0) / (x1 - x0) * plot_w

    def sy(y: float) -> float:
        return top + (y1 - y) / (y1 - y0) * plot_h

    parts = [
        '<div class="chart-card">',
        f'<div class="chart-title">{escape(title)}</div>',
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" class="plot-bg"/>',
    ]
    for tick in range(5):
        y = top + tick * plot_h / 4
        value = y1 - tick * (y1 - y0) / 4
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" class="grid"/>')
        parts.append(
            f'<text x="{left-8}" y="{y+4:.2f}" text-anchor="end" class="axis-text">'
            f'{escape(fmt(value, 2, unit))}</text>'
        )
    for index, (label, points) in enumerate(prepared):
        color = _PALETTE[index % len(_PALETTE)]
        polyline = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in points)
        parts.append(
            f'<polyline points="{polyline}" fill="none" stroke="{color}" '
            'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        legend_y = 14 + index * 14
        parts.append(f'<line x1="{left+8}" y1="{legend_y}" x2="{left+24}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{left+30}" y="{legend_y+4}" class="legend">{escape(label)}</text>')
    parts.append(f'<text x="{left}" y="{height-8}" class="axis-text">{escape(start.strftime("%H:%M:%S"))}</text>')
    parts.append(f'<text x="{width-right}" y="{height-8}" text-anchor="end" class="axis-text">{escape(end.strftime("%H:%M:%S"))}</text>')
    parts.extend(['</svg>', '</div>'])
    return "".join(parts)


def event_svg(event: dict[str, Any]) -> str:
    evidence = event.get("evidence", {}) if isinstance(event.get("evidence"), dict) else {}
    buckets = evidence.get("buckets", []) if isinstance(evidence.get("buckets"), list) else []
    points = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        metrics = bucket.get("metrics_changed", [])
        count = len(metrics) if isinstance(metrics, list) else 0
        points.append((bucket.get("timestamp"), count))
    if not points:
        return '<div class="chart-nd small">Sin buckets anómalos graficables.</div>'
    timestamps = [_dt(ts) for ts, _ in points]
    valid = [item for item in timestamps if item is not None]
    start = min(valid) if valid else None
    end = max(valid) if valid else None
    return svg_chart(
        "Señales simultáneas por bucket",
        [{"label": "métricas alteradas", "points": points}],
        global_start=start,
        global_end=end,
        unit="",
        height=150,
    )


def _metric_rows(metrics: dict[str, Any]) -> str:
    labels = [
        ("Disponibilidad observada", "availability_observed_pct", "%"),
        ("RTT mediano", "rtt_median_ms", " ms"),
        ("RTT P95", "rtt_p95_ms", " ms"),
        ("RTT P99", "rtt_p99_ms", " ms"),
        ("Pérdida observada", "loss_percent", "%"),
        ("Delay variation P95", "delay_variation_p95_ms", " ms"),
        ("Utilización media", "utilization_mean_pct", "%"),
        ("Utilización P95", "utilization_p95_pct", "%"),
        ("Utilización máxima", "utilization_max_pct", "%"),
        ("RX drops", "rx_drops_delta_total", ""),
        ("TX drops", "tx_drops_delta_total", ""),
        ("Eventos relevantes", "event_count", ""),
    ]
    rows = []
    for label, key, suffix in labels:
        value = metrics.get(key)
        rendered = fmt(value, 2, suffix) if key != "event_count" else (ND if value is None else str(int(value)))
        rows.append(f"<tr><th>{escape(label)}</th><td>{escape(rendered)}</td></tr>")
    return "".join(rows)


def _quality_table(payload: dict[str, Any]) -> str:
    quality = payload.get("dataset_quality", {})
    sources = quality.get("sources", {}) if isinstance(quality, dict) else {}
    rows = []
    for name in ("interface", "host", "probes"):
        row = sources.get(name, {}) if isinstance(sources, dict) else {}
        ts = row.get("timestamp_quality", {}) if isinstance(row.get("timestamp_quality"), dict) else {}
        rows.append(
            "<tr>"
            f"<td>{escape(name)}</td>"
            f"<td>{escape(str(row.get('expected_rows', ND)))}</td>"
            f"<td>{escape(str(row.get('actual_rows', ND)))}</td>"
            f"<td>{escape(str(row.get('valid_rows', ND)))}</td>"
            f"<td>{escape(fmt(row.get('coverage_pct'), 2, '%'))}</td>"
            f"<td>{escape(str(ts.get('gaps', ND)))}</td>"
            f"<td>{escape(str(ts.get('duplicates', ND)))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _checks(payload: dict[str, Any]) -> str:
    checks = payload.get("dataset_quality", {}).get("checks", [])
    result = []
    for check in checks:
        status = str(check.get("status", "WARN"))
        result.append(
            f'<div class="check {escape(status.lower())}">'
            f'<strong>{escape(status)}</strong> {escape(str(check.get("id", "")))} — '
            f'{escape(str(check.get("detail", "")))}</div>'
        )
    return "".join(result)


def _findings(payload: dict[str, Any]) -> str:
    findings = payload.get("findings", [])
    fingerprints = {
        str(row.get("event_id")): row
        for row in payload.get("fingerprints", [])
        if isinstance(row, dict)
    }
    if not findings:
        return '<div class="empty">Evidence Engine no generó hallazgos durante esta ventana.</div>'
    cards = []
    for finding in findings:
        event_id = str(finding.get("event_id", ND))
        fp = fingerprints.get(event_id, {})
        vector = fp.get("compact_vector", {}) if isinstance(fp.get("compact_vector"), dict) else {}
        cards.append(
            '<article class="event-card">'
            f'<div class="event-head"><span>{escape(str(finding.get("finding_id", ND)))}</span>'
            f'<span>{escape(event_id)}</span><span>Confianza {escape(str(finding.get("confidence", ND)))}</span></div>'
            f'<p><b>Ventana:</b> {escape(_clock(finding.get("start")))} → {escape(_clock(finding.get("end")))} '
            f'({_duration(finding.get("duration_seconds"))})</p>'
            f'<p><b>HECHO.</b> {escape(str(finding.get("fact", ND)))}</p>'
            f'<p><b>INTERPRETACIÓN.</b> {escape(str(finding.get("interpretation", ND)))}</p>'
            f'<p><b>HIPÓTESIS.</b> {escape(str(finding.get("hypothesis", ND)))}</p>'
            f'<p><b>CONFIANZA.</b> {escape(str(finding.get("confidence", ND)))} — '
            f'{escape(str(finding.get("confidence_basis", "")))}</p>'
            f'<p><b>RECOMENDACIÓN.</b> {escape(str(finding.get("recommendation", ND)))}</p>'
            '<div class="fingerprint">'
            f'<span>Δutil {escape(fmt(vector.get("delta_utilization_pp"), 2, " pp"))}</span>'
            f'<span>ΔRTT {escape(fmt(vector.get("delta_rtt_pct"), 2, "%"))}</span>'
            f'<span>Δdelay {escape(fmt(vector.get("delta_delay_variation_pct"), 2, "%"))}</span>'
            f'<span>Δloss {escape(fmt(vector.get("delta_loss_pp"), 2, " pp"))}</span>'
            f'<span>Δdrops {escape(fmt(vector.get("delta_drops"), 0, ""))}</span>'
            '</div>'
            + event_svg(finding.get("event", {}) if isinstance(finding.get("event"), dict) else {})
            + '</article>'
        )
    return "".join(cards)


def render_html(payload: dict[str, Any], charts: dict[str, str]) -> str:
    executive = payload.get("executive_summary", {})
    metrics = payload.get("headline_metrics", {})
    methodology = payload.get("methodology", [])
    limitations = payload.get("limitations", [])
    recommendations_note = payload.get("recommendations_note")
    commercial = payload.get("commercial_next_step")
    source_rows = []
    for name, digest in payload.get("source_sha256", {}).items():
        source_rows.append(f"<tr><td>{escape(name)}</td><td><code>{escape(str(digest))}</code></td></tr>")

    css = """
    :root{--ink:#172033;--muted:#667085;--line:#d9dee8;--soft:#f5f7fb;--brand:#163b72;--ok:#166534;--warn:#92400e;--fail:#991b1b}
    *{box-sizing:border-box} body{font-family:Inter,Segoe UI,Arial,sans-serif;margin:0;color:var(--ink);background:#edf1f7;line-height:1.48}
    main{max-width:1080px;margin:30px auto;background:white;box-shadow:0 8px 30px #1b2a4120}
    section{padding:34px 46px;border-bottom:1px solid var(--line)} .cover{min-height:520px;display:flex;flex-direction:column;justify-content:center;background:linear-gradient(145deg,#102f5d,#1d4f91);color:white}
    .eyebrow{font-size:13px;letter-spacing:.16em;text-transform:uppercase;opacity:.8}.cover h1{font-size:46px;margin:12px 0 4px}.cover h2{font-weight:400;margin:0 0 40px}.meta-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px 30px;max-width:720px}
    h2{font-size:28px;margin:0 0 20px;color:#173968} h3{margin:22px 0 10px}.lede{font-size:18px}.badge{display:inline-block;padding:5px 10px;border-radius:999px;font-weight:700;background:#e8edf6}.pass{color:var(--ok)}.warn{color:var(--warn)}.fail{color:var(--fail)}
    table{width:100%;border-collapse:collapse;margin:12px 0 22px}th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--soft)}code{font-size:11px;word-break:break-all}
    .metric-table th{width:55%}.chart-grid{display:grid;grid-template-columns:1fr;gap:16px}.chart-card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:white}.chart-title{font-weight:700;margin:2px 0 8px}.chart-nd{padding:30px;color:var(--muted);background:var(--soft);border-radius:8px}.chart-nd.small{padding:14px}.plot-bg{fill:#fbfcfe}.grid{stroke:#e4e7ec;stroke-width:1}.axis-text,.legend{font-size:10px;fill:#667085}
    .check{padding:8px 10px;margin:6px 0;border-left:4px solid #98a2b3;background:#fafafa}.check.pass{border-color:#16a34a}.check.warn{border-color:#d97706}.check.fail{border-color:#dc2626}
    .event-card{border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0}.event-head{display:flex;gap:10px;flex-wrap:wrap}.event-head span{background:#eef3fb;padding:4px 9px;border-radius:8px;font-weight:700}.fingerprint{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.fingerprint span{background:#f4f4f5;padding:5px 8px;border-radius:6px}.empty{padding:18px;background:var(--soft);color:var(--muted);border-radius:8px}
    .note{padding:14px 16px;background:#fff7ed;border-left:4px solid #f59e0b}.good{padding:14px 16px;background:#f0fdf4;border-left:4px solid #22c55e}.footer{color:var(--muted);font-size:12px}
    ul{padding-left:22px}@media print{body{background:white}main{margin:0;max-width:none;box-shadow:none}.cover{min-height:95vh}section{break-inside:avoid}.event-card{break-inside:avoid}} 
    """

    methods = "".join(f"<li>{escape(str(item))}</li>" for item in methodology)
    limits = "".join(f"<li>{escape(str(item))}</li>" for item in limitations)
    commercial_html = ""
    if isinstance(commercial, dict):
        commercial_html = (
            '<section><h2>Siguiente etapa comercial</h2>'
            f'<p>{escape(str(commercial.get("rationale", "")))}</p>'
            '<table><tr><th>Etapa</th><th>Alcance sugerido</th></tr>'
            + "".join(
                f'<tr><td>{escape(str(row.get("stage", "")))}</td><td>{escape(str(row.get("scope", "")))}</td></tr>'
                for row in commercial.get("options", [])
            )
            + '</table></section>'
        )

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(str(payload.get('company', 'HACHENET')))} — Link Health Audit</title><style>{css}</style></head>
<body><main>
<section class="cover"><div class="eyebrow">NetTwin LinkProbe v0.3</div><h1>{escape(str(payload.get('company', 'HACHENET')))}</h1><h2>LINK HEALTH AUDIT</h2><div class="meta-grid">
<div><b>Enlace</b><br>{escape(str(payload.get('link_name', ND)))}</div><div><b>Run ID</b><br>{escape(str(payload.get('run_id', ND)))}</div>
<div><b>Ventana observada</b><br>{escape(_duration(payload.get('observed_duration_seconds')))}</div><div><b>Sensor</b><br>{escape(str(payload.get('sensor_version', ND)))}</div>
<div><b>Inicio</b><br>{escape(_clock(payload.get('started_utc')))}</div><div><b>Fin</b><br>{escape(_clock(payload.get('ended_utc')))}</div>
</div></section>
<section><h2>Resumen ejecutivo</h2><p class="lede">{escape(str(executive.get('summary', '')))}</p><p class="{escape(str(executive.get('class', 'note')))}">{escape(str(executive.get('key_message', '')))}</p></section>
<section><h2>Calidad del dataset</h2><p>Estado: <span class="badge {escape(str(payload.get('dataset_quality', {}).get('status', 'WARN')).lower())}">{escape(str(payload.get('dataset_quality', {}).get('status', ND)))}</span> · Conclusiones habilitadas: <b>{'SÍ' if payload.get('dataset_quality', {}).get('safe_for_conclusions') else 'NO'}</b></p>
<table><thead><tr><th>Fuente</th><th>Esperadas</th><th>Recibidas</th><th>Válidas</th><th>Cobertura</th><th>Huecos</th><th>Duplicados</th></tr></thead><tbody>{_quality_table(payload)}</tbody></table>{_checks(payload)}</section>
<section><h2>Tabla principal de resultados</h2><p>Target principal para métricas de calidad: <b>{escape(str(payload.get('primary_target', ND)))}</b>. La disponibilidad es exclusivamente la observada en esta ventana.</p><table class="metric-table"><tbody>{_metric_rows(metrics)}</tbody></table></section>
<section><h2>Línea temporal maestra</h2><p>Todos los gráficos comparten la misma escala temporal para facilitar la comparación visual. N/D significa que la métrica no fue medible con la evidencia disponible.</p><div class="chart-grid">{''.join(charts.get(key, '') for key in ('utilization','rtt','delay','loss','drops','cpu'))}</div></section>
<section><h2>Eventos y evidencia</h2>{_findings(payload)}{f'<p class="note">{escape(str(recommendations_note))}</p>' if recommendations_note else ''}</section>
<section><h2>Metodología</h2><ul>{methods}</ul></section>
<section><h2>Limitaciones</h2><ul>{limits}</ul></section>
<section><h2>Anexos técnicos</h2><p>SHA-256 de las fuentes utilizadas para generar este informe:</p><table><thead><tr><th>Artefacto</th><th>SHA-256</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table><p><b>Report SHA-256:</b> <code>{escape(str(payload.get('report_sha256', ND)))}</code></p></section>
{commercial_html}
<section class="footer">Informe generado de forma determinista a partir del run sellado. Correlación no implica causalidad. No se capturó payload ni contenido de comunicaciones.</section>
</main></body></html>"""
