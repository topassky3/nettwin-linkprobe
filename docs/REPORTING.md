# Fase 13 — Report Engine / Link Health Audit

## Objetivo

Transformar un run sellado de LinkProbe en un informe técnico reproducible sin modificar la evidencia original.

El flujo público es:

```text
run sellado
  -> verify-integrity --strict
  -> Dataset Quality Gate
  -> Quality Engine
  -> Event Engine
  -> Correlation Engine
  -> Evidence Engine
  -> Event Fingerprints
  -> analysis.json
  -> report.json
  -> report.html
  -> report.pdf opcional
```

## Regla de evidencia

El informe se genera siempre fuera del directorio sellado. `checksums.sha256` y los archivos crudos no se modifican.

Toda métrica no medible permanece `null` en JSON y `N/D` en HTML. La velocidad reportada por la NIC nunca sustituye la capacidad explícita del servicio.

## Dataset Quality Gate

Antes de ejecutar conclusiones se revisan:

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
├── analysis.json
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
- enlace;
- run_id;
- versión del sensor;
- ventana observada;
- duración;
- estado de integridad;
- estado del Quality Gate.

El resumen ejecutivo distingue explícitamente hechos observados de hipótesis. Si no existen findings, no se generan recomendaciones específicas ni se inventa una causa.

## Tabla principal

Incluye, cuando son medibles:

- disponibilidad observada;
- RTT mediano/P95/P99;
- pérdida;
- variación temporal de RTT;
- utilización media/P95/máxima solo con capacidad explícita;
- drops;
- número de eventos.

## Timeline

El HTML incorpora SVG autocontenido para las series disponibles, sin CDN ni JavaScript externo. Las métricas no disponibles se muestran como `N/D`.

## Eventos y evidencia

Cada finding conserva la cadena:

```text
HECHO
-> INTERPRETACIÓN
-> HIPÓTESIS
-> CONFIANZA
-> RECOMENDACIÓN
```

`causal_claim_allowed=false`. Correlación y coincidencia temporal no se presentan como causalidad.

## Reproducibilidad

`report.json`, `report.html` y `analysis.json` son deterministas para el mismo run/configuración. No se incluye un timestamp de generación tomado del reloj de pared.

El reporte incorpora SHA-256 de las fuentes y `report_sha256`.

## PDF

`--pdf` intenta generar `report.pdf` con WeasyPrint. Si la dependencia no está disponible, HTML sigue siendo la salida canónica y el CLI informa el motivo.

## Validación operacional

Además de unit tests, Fase 13 incluye smoke tests de caja negra que ejecutan los mismos entrypoints públicos usados desde PowerShell:

```powershell
python -m unittest tests.test_phase13_acceptance_cli -v
```

Los smoke tests ejecutan:

```text
python scripts/generate_report_debug_run.py --output ...
python nettwin.py report --run ... --output ...
```

y validan exit codes y artefactos JSON/HTML reales.

### Portabilidad de stdout en Windows

Los tests no fuerzan `encoding="utf-8"` al capturar `subprocess`. El proceso padre usa la codificación local compatible con el proceso hijo. Además, la aceptación no depende de comparar frases acentuadas como `VÁLIDA` o `SÍ`: los estados canónicos se validan mediante exit code, `dataset_quality.json`, `report.json` y `analysis.json`.

Esto evita falsos negativos causados exclusivamente por la página de códigos de la consola de Windows.

## Comando público

```powershell
python nettwin.py report `
  --run <run_dir> `
  --output <report_dir> `
  --company "HacheNet" `
  --link "LINK-01"
```

Capacidad conocida de forma explícita:

```powershell
python nettwin.py report `
  --run <run_dir> `
  --output <report_dir> `
  --capacity-mbps 100
```

Nunca inferir capacidad del servicio desde `reported_link_speed_mbps`.
