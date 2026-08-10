# Fase 14 — Criterios de aceptación

## Base

Fase 13 quedó validada con:

```text
179/179 tests
```

Fase 14 añade 9 pruebas:

```text
7/7  DryRun Engine
2/2  CLI público
188/188 suite completa
```

## 1. Tests específicos

```powershell
python -m unittest tests.test_dry_run_engine -v
python -m unittest tests.test_phase14_cli -v
```

Esperado:

```text
Ran 7 tests
OK

Ran 2 tests
OK
```

## 2. Suite completa

```powershell
python -m unittest discover -s tests -v
```

Esperado:

```text
Ran 188 tests
OK
```

## 3. Dry-run real local

El criterio operacional principal de Fase 14 se valida con un único comando:

```powershell
python nettwin.py dry-run `
  --output debug_fase14_live_01 `
  --duration-seconds 12 `
  --interval-seconds 2 `
  --run-id LOCAL-DRYRUN-001 `
  --company "HacheNet" `
  --link "LOCAL-WIFI-DRYRUN"
```

Después de iniciar el comando no debe requerirse ninguna intervención manual para pasar de captura a integridad, análisis e informe.

El target activo debe ser exclusivamente:

```text
local_loopback -> 127.0.0.1
```

La salida ideal:

```text
Dry-run Fase 14 finalizado.
Estado: PASS
Captura: completed
Integridad de captura: VÁLIDA
Quality Gate: PASS
Análisis ejecutado: SÍ
Tráfico activo externo ejecutado por dry-run: NO
```

Exit code esperado:

```text
0
```

## 4. Manifiesto de Fase 14

```powershell
$d = Get-Content `
  debug_fase14_live_01\dry_run_summary.json `
  -Encoding UTF8 |
  ConvertFrom-Json

$d |
Select-Object schema_version,
              phase,
              mode,
              status,
              failed_stage,
              automatic_execution,
              manual_intervention_after_start,
              run_id |
Format-List
```

Esperado:

```text
schema_version                  : linkprobe-dry-run-v1
phase                           : 14
mode                            : dry-run-local
status                          : PASS
failed_stage                    :
automatic_execution             : True
manual_intervention_after_start : False
```

## 5. Etapas

```powershell
$d.stages | Format-List
```

Esperado:

```text
prepare                 : PASS
preflight_capture       : PASS
integrity_before_report : PASS
analysis_report         : PASS
integrity_after_report  : PASS
```

## 6. Seguridad

```powershell
$d.active_probe | Format-List
$d.security | Format-List
```

Esperado:

```text
target_address                   : 127.0.0.1
scope                            : loopback_only
external_network_probe_performed : False

payload_capture                   : False
customer_communications_inspected : False
network_configuration_modified    : False
firewall_modified                 : False
routing_modified                  : False
unauthorized_discovery            : False
external_network_probe_performed  : False
```

## 7. Captura

```powershell
$d.capture | ConvertTo-Json -Depth 10
```

Debe indicar:

```text
status          = completed
stop_reason     = duration_elapsed
preflight_ready = true
integrity_valid = true
```

y todos los artefactos de captura en `true`.

Con 12 s / 2 s se esperan aproximadamente seis ciclos por collector. La temporización real puede producir pequeñas diferencias operacionales, pero no deben existir fallos fatales ni pérdida de integridad.

## 8. Reporte

```powershell
$d.report | ConvertTo-Json -Depth 10
```

Debe indicar:

```text
integrity_valid      = true
quality_status       = PASS
safe_for_conclusions = true
analysis_executed    = true
```

y todos los artefactos de reporte en `true`.

Un dry-run local corto puede producir:

```text
event_count       = 0
finding_count     = 0
fingerprint_count = 0
```

sin que eso sea un fallo. Lo obligatorio es que los Engines correspondientes se ejecuten y sus artefactos existan.

## 9. Run sellado preservado

```powershell
$d.integrity_preservation | Format-List
```

Esperado:

```text
valid_after_report    : True
sealed_run_unchanged  : True
```

Los valores:

```text
checksums_sha256_before
checksums_sha256_after
```

deben ser idénticos.

Además:

```powershell
python nettwin.py verify-integrity `
  --run-dir debug_fase14_live_01\run `
  --strict-untracked
```

Debe terminar `Integridad: VÁLIDA`.

## 10. Artefactos

```powershell
Get-ChildItem debug_fase14_live_01 -Recurse |
Select-Object FullName,Length
```

Deben existir como mínimo:

### Captura

- `dry_run_config.json`
- `run/preflight.json`
- `run/interface_samples.csv`
- `run/host_samples.csv`
- `run/probe_samples.csv`
- `run/orchestration.json`
- `run/run_metadata.json`
- `run/checksums.sha256`
- `run/logs/linkprobe.log`

### Análisis/informe

- `report/dataset_quality.json`
- `report/analysis.json`
- `report/report.json`
- `report/report.html`
- `report/analysis/events.json`
- `report/analysis/correlations.json`
- `report/analysis/evidence_records.json`
- `report/analysis/event_fingerprints.json`

### Fase 14

- `dry_run_summary.json`

## 11. Prueba de no sobrescritura

Ejecutar el mismo comando otra vez sobre el mismo `--output` debe ser rechazado antes de iniciar captura.

Esto demuestra que Fase 14 no mezcla evidencia entre dry-runs.

## Definition of Done — Fase 14

La fase queda cerrada cuando se confirma:

```text
7/7 DryRun Engine                    ✅
2/2 CLI público                      ✅
188/188 suite completa               ✅

un solo comando                      ✅
sin intervención manual              ✅
Preflight real                       ✅
captura real                         ✅
Interface Collector                  ✅
Host Collector                       ✅
Active Probe Engine                  ✅
loopback solamente                   ✅
almacenamiento crudo                 ✅
integridad strict                    ✅
Quality Gate                         ✅
Event Engine ejecutado               ✅
Correlation Engine ejecutado         ✅
Evidence Engine ejecutado            ✅
Fingerprint Engine ejecutado         ✅
report.json                          ✅
report.html                          ✅
run sellado sin cambios              ✅
dry_run_summary.json                 ✅
tráfico activo externo = NO          ✅
```

Solo después de esta validación se pasa al piloto HacheNet de 4 horas.
