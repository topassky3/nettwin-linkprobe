# NetTwin LinkProbe v0.3 — Dry-run local

## Objetivo

La Fase 14 valida el pipeline completo antes de instalar NetTwin LinkProbe en HacheNet.

Un único comando debe ejecutar, sin intervención manual intermedia:

```text
selección de interfaz local
        ↓
configuración externa segura
        ↓
Preflight
        ↓
Interface Collector + Host Collector + Active Probe Engine
        ↓
almacenamiento de datos crudos
        ↓
integridad SHA-256 estricta
        ↓
Dataset Quality Gate
        ↓
Quality / Events / Correlations / Evidence / Fingerprints
        ↓
Link Health Audit
        ↓
verificación final del run sellado
        ↓
dry_run_summary.json
```

## Seguridad del dry-run

El modo `dry-run` es deliberadamente local:

- único target activo: `127.0.0.1`;
- nombre de target: `local_loopback`;
- ICMP payload: 32 bytes;
- un echo por ciclo;
- no escanea red local;
- no descubre equipos;
- no captura payload;
- no inspecciona comunicaciones;
- no modifica rutas;
- no modifica firewall;
- no modifica interfaces;
- no modifica servicios;
- no ejecuta probes hacia Internet.

El preflight puede inspeccionar de forma read-only el contexto de rutas/gateway, como en Fase 11, pero no envía probes a targets.

## CLI

Ejemplo recomendado en Windows:

```powershell
python nettwin.py dry-run `
  --output debug_fase14_live_01 `
  --duration-seconds 12 `
  --interval-seconds 2 `
  --run-id LOCAL-DRYRUN-001 `
  --company "HacheNet" `
  --link "LOCAL-WIFI-DRYRUN"
```

Si `--interface` se omite, el selector prefiere una interfaz física UP con contadores e IPv4 útil. Preflight sigue siendo la autoridad que decide si la interfaz está lista.

Puede indicarse manualmente:

```powershell
python nettwin.py dry-run `
  --output debug_fase14_live_01 `
  --interface "Wi-Fi" `
  --duration-seconds 12 `
  --interval-seconds 2
```

## Capacidad

`--capacity-mbps` solo debe utilizarse cuando se conoce explícitamente la capacidad real del enlace que se desea modelar.

Si se omite:

```text
utilización = N/D
```

La velocidad reportada por la NIC no se utiliza como capacidad del servicio.

## Workspace

El workspace debe ser nuevo o estar vacío. El dry-run rechaza un directorio que ya contenga evidencia para evitar mezclar o sobrescribir ejecuciones.

Estructura esperada:

```text
debug_fase14_live_01/
├── dry_run_config.json
├── dry_run_summary.json
├── run/
│   ├── experiment_config.json
│   ├── preflight.json
│   ├── interface_samples.csv
│   ├── host_samples.csv
│   ├── probe_samples.csv
│   ├── orchestration.json
│   ├── run_metadata.json
│   ├── checksums.sha256
│   ├── integrity_report.json
│   └── logs/
│       └── linkprobe.log
└── report/
    ├── dataset_quality.json
    ├── dataset_quality_summary.csv
    ├── analysis.json
    ├── report.json
    ├── report.html
    └── analysis/
        ├── quality_summary.json
        ├── events.json
        ├── correlations.json
        ├── evidence_records.json
        └── event_fingerprints.json
```

## Criterio PASS

El dry-run solo termina `PASS` cuando simultáneamente:

1. Preflight aprueba el entorno.
2. Orchestrator termina `completed`.
3. La captura queda sellada y `verify-integrity --strict` es válida.
4. Están presentes todos los artefactos crudos obligatorios.
5. Dataset Quality Gate habilita conclusiones.
6. El pipeline analítico se ejecuta.
7. Se generan los artefactos Quality/Event/Correlation/Evidence/Fingerprint.
8. Se generan `analysis.json`, `report.json` y `report.html`.
9. Después del reporte, el run vuelve a pasar `verify-integrity --strict`.
10. El SHA-256 de `checksums.sha256` es idéntico antes y después del reporte.

No se exige que un dry-run corto y tranquilo genere un evento real. Lo obligatorio es que el Event Engine se ejecute y produzca su artefacto reproducible; un conteo de cero eventos es un resultado válido si la evidencia no supera los umbrales.

## Resultado

`dry_run_summary.json` es el manifiesto operativo de Fase 14. Debe mostrar:

```text
status = PASS
failed_stage = null
automatic_execution = true
manual_intervention_after_start = false
external_network_probe_performed = false
```

y todas las etapas:

```text
prepare                 PASS
preflight_capture       PASS
integrity_before_report PASS
analysis_report         PASS
integrity_after_report  PASS
```

La aprobación de Fase 14 significa que la cadena técnica puede ejecutarse localmente completa antes del piloto, no que la red de HacheNet haya sido validada todavía.
