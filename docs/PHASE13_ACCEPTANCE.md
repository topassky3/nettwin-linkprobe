# Fase 13 — Criterios de aceptación

La fase no se considera cerrada hasta validar localmente todos los puntos.

## Suite

Fase 12 terminó con 156 pruebas. Fase 13 añade 21 pruebas.

Esperado:

```text
9/9   Dataset Quality
9/9   Report Engine
1/1   Report CLI / analysis.json
2/2   Acceptance CLI de caja negra
177/177 suite completa
```

## Acceptance CLI real

Antes de la suite completa deben pasar los dos smoke tests que ejecutan los entrypoints públicos con `subprocess`, igual que PowerShell:

```powershell
python -m unittest tests.test_phase13_acceptance_cli -v
```

Estos tests validan:

- `python scripts/generate_report_debug_run.py --output ...` ejecutado por ruta;
- `python nettwin.py report ...` de extremo a extremo;
- exit code `0`;
- creación del run sellado;
- Quality Gate `PASS`;
- `dataset_quality.json`, `analysis.json`, `report.json` y `report.html`;
- al menos un evento, finding y fingerprint;
- `causal_inference_performed=false`.

Las pruebas no dependen de la codificación exacta con la que Windows renderice caracteres acentuados en stdout. Los estados canónicos se validan mediante exit code y artefactos JSON.

## Run sintético rico

```powershell
python scripts\generate_report_debug_run.py --output debug_fase13
python nettwin.py report `
  --run debug_fase13\run `
  --output debug_fase13\report `
  --company "HacheNet" `
  --link "LINK-01"
```

Debe producir:

- integridad estricta válida;
- Quality Gate `PASS`;
- conclusiones habilitadas;
- pipeline analítico ejecutado;
- `dataset_quality.json`;
- `dataset_quality_summary.csv`;
- artefactos Quality/Event/Correlation/Evidence/Fingerprint;
- `analysis.json`;
- `report.json`;
- `report.html`;
- al menos un evento sintético;
- al menos un finding;
- al menos un fingerprint;
- tabla de métricas;
- seis gráficos de línea temporal;
- gráfico por evento;
- metodología;
- limitaciones;
- hashes SHA-256 de fuentes.

## Run real de Fase 12

El mismo comando debe poder ejecutarse sobre `debug_fase12_run2\run`.

En ese run local, RTT puede ser `N/D` si Windows no expone un RTT parseable para `tiempo<1m`; esto no debe convertirse en pérdida. Reachability y loss deben conservar el resultado real de paquetes.

## Capacidad desconocida

Sin capacidad explícita:

```text
utilization_mean_pct = null
utilization_p95_pct = null
utilization_max_pct = null
```

y HTML debe mostrar `N/D`.

Nunca se usa `reported_link_speed_mbps` como capacidad del servicio.

## Quality Gate FAIL

Un run íntegro pero con interfaz configurada distinta de la observada debe:

- generar reporte diagnóstico;
- producir Quality Gate `FAIL`;
- `safe_for_conclusions=false`;
- `analysis_executed=false`;
- no inventar eventos ni recomendaciones.

## Integridad del run

Generar el reporte no debe cambiar el SHA-256 de `checksums.sha256`.

El directorio de salida del reporte debe estar fuera del run sellado.

## Determinismo

Generar el mismo reporte dos veces en directorios externos distintos debe producir:

```text
report-a/report.json == report-b/report.json
report-a/report.html == report-b/report.html
report-a/analysis.json == report-b/analysis.json
```

byte a byte.

## DoD del informe

- portada ✅
- resumen ejecutivo ✅
- metodología ✅
- calidad del dataset ✅
- tabla de métricas ✅
- línea temporal ✅
- eventos relevantes ✅
- hechos ✅
- interpretaciones ✅
- hipótesis ✅
- confianza ✅
- recomendaciones ligadas a evidencia ✅
- limitaciones ✅
- anexos técnicos ✅
- `analysis.json` ✅
- HTML ✅
- PDF opcional cuando WeasyPrint está disponible ✅
