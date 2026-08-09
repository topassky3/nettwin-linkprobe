# Fase 13 — Report Engine / Link Health Audit

## Objetivo

Fase 13 transforma un run sellado de NetTwin LinkProbe en un informe técnico reproducible y defendible.

El orden es obligatorio:

```text
run sellado
  -> verify-integrity --strict
  -> Dataset Quality Gate
  -> Quality Engine
  -> Event Engine
  -> Correlation Engine
  -> Evidence Engine
  -> Event Fingerprints
  -> report.json
  -> report.html
  -> report.pdf opcional
```

El informe se escribe **fuera** del directorio sellado del run. Esto evita que la generación del reporte convierta los artefactos nuevos en archivos no rastreados del dataset original.

## CLI

```powershell
python nettwin.py report `
  --run runs\HACHENET-20260809-LINK01-001 `
  --output reports\HACHENET-20260809-LINK01-001 `
  --company "HacheNet" `
  --link "LINK-01" `
  --capacity-mbps 100
```

`--capacity-mbps` es opcional. Si no existe una capacidad explícita en el CLI o en `experiment_config.json`, la utilización permanece `N/D`. Nunca se usa `reported_link_speed_mbps` como sustituto de capacidad del servicio.

`--pdf` intenta utilizar WeasyPrint si está disponible. HTML siempre es la salida primaria y no depende de Internet, CDN ni JavaScript externo.

## Dataset Quality Gate

Antes de ejecutar conclusiones se revisa:

- integridad SHA-256;
- muestras esperadas, recibidas y válidas;
- cobertura;
- timestamps inválidos;
- timestamps fuera de orden;
- duplicados;
- huecos temporales;
- coherencia con la ventana orquestada;
- interfaz esperada frente a observada;
- resets/overflow de counters;
- valores imposibles;
- targets no alcanzables;
- contexto de zona horaria.

### Estados

- `PASS`: no se detectaron problemas de calidad relevantes.
- `WARN`: existen limitaciones visibles, pero no invalidan necesariamente conclusiones descriptivas.
- `FAIL`: existe un problema bloqueante. El informe diagnóstico se genera, pero el pipeline analítico y las conclusiones se bloquean.

Son bloqueantes, entre otros:

- integridad estricta inválida;
- interfaz incorrecta;
- timestamp inválido;
- valor físicamente/lógicamente imposible;
- cobertura o porcentaje de muestras válidas por debajo del umbral de fallo.

Un target no alcanzable se considera una condición observada y genera advertencia; no se convierte por sí solo en una afirmación de causa.

## Salidas

```text
reports/<RUN_ID>/
├── dataset_quality.json
├── dataset_quality_summary.csv
├── report.json
├── report.html
├── report.pdf                  # solo con --pdf y WeasyPrint
└── analysis/
    ├── quality_summary.json
    ├── quality_interface_summary.csv
    ├── quality_probe_summary.csv
    ├── quality_interface_processed.csv
    ├── quality_probe_processed.csv
    ├── events.json
    ├── event_timeline.csv
    ├── event_summary.csv
    ├── correlations.json
    ├── correlation_summary.csv
    ├── correlation_aligned_pairs.csv
    ├── evidence_records.json
    ├── evidence_summary.csv
    ├── evidence_trace.csv
    ├── event_fingerprints.json
    ├── event_fingerprint_summary.csv
    └── event_fingerprint_trace.csv
```

## Portada y resumen ejecutivo

La portada contiene:

- empresa;
- Link Health Audit;
- enlace;
- run_id;
- ventana observada;
- versión del sensor;
- inicio y fin.

El resumen ejecutivo se genera únicamente a partir de los resultados medidos. Si Quality Gate falla, indica explícitamente que las conclusiones técnicas están bloqueadas.

## Tabla principal

Incluye, cuando son medibles:

- disponibilidad observada;
- RTT mediano;
- RTT P95;
- RTT P99;
- pérdida observada;
- delay variation P95;
- utilización media;
- utilización P95;
- utilización máxima;
- RX drops;
- TX drops;
- eventos relevantes.

Todo valor no medible aparece como `null` en `report.json` y `N/D` en HTML.

## Línea temporal maestra

`report.html` genera SVG autocontenido para:

- utilización;
- RTT;
- variación temporal de retardo;
- pérdida;
- drops;
- CPU del host.

Todos usan la misma escala temporal global. Esto facilita observar coincidencia temporal entre métricas sin convertirla en causalidad.

## Eventos y Evidence Engine

Cada hallazgo muestra:

```text
HECHO
INTERPRETACIÓN
HIPÓTESIS
CONFIANZA
RECOMENDACIÓN
```

También incorpora el fingerprint del evento y una gráfica de señales simultáneas por bucket.

Las recomendaciones provienen del Evidence Engine y se relacionan con evidencia concreta. Si no hay hallazgos, el informe no inventa recomendaciones técnicas para llenar espacio.

## Limitaciones obligatorias

El informe conserva explícitamente, entre otras, estas limitaciones:

- una ventana de cuatro horas caracteriza solo el periodo observado;
- disponibilidad observada no equivale a SLA histórico;
- correlación no implica causalidad;
- no se afirma causa física exacta sin evidencia;
- valores no medibles quedan N/D.

## Etapa comercial

Solo se muestra después de los resultados técnicos y únicamente cuando existen hallazgos Evidence Engine. Se presenta como una posible continuación para confirmar el comportamiento, no como conclusión técnica.

## Reproducibilidad

`report.json` no incorpora la hora de generación de pared. Usa timestamps del run y hashes SHA-256 de las fuentes.

Dos reportes generados con:

- mismo run;
- misma configuración;
- misma versión de software;
- mismos argumentos;

deben producir `report.json` y `report.html` idénticos byte a byte.

## Seguridad

Report Engine es offline respecto del enlace. No ejecuta probes, no resuelve targets, no captura payload, no modifica routing/firewall/interfaces y no inspecciona comunicaciones. Solo lee el run existente y escribe en el directorio externo de reporte.
