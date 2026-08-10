# Fase 15 — Criterios de aceptación

## Alcance

Fase 15 prepara NetTwin LinkProbe v0.3 para el piloto real de HacheNet de cuatro horas y proporciona una aceptación local separada que no envía tráfico fuera del host.

La aceptación local valida el software/readiness. La Fase 15 productiva solo se cierra después de ejecutar la ventana real autorizada de 4 h.

## Tests automáticos

Fase 14 cerró con 188 pruebas. Fase 15 añade 13:

```text
11/11 Pilot Engine
2/2 CLI público
201/201 suite completa
```

No cerrar el software/readiness si existe cualquier FAIL o ERROR.

### Orden recomendado

```powershell
python -W error::DeprecationWarning -m unittest tests.test_pilot_engine -v
```

Debe terminar:

```text
Ran 11 tests
OK
```

Después:

```powershell
python -W error::DeprecationWarning -m unittest tests.test_phase15_cli -v
```

Debe terminar:

```text
Ran 2 tests
OK
```

Finalmente:

```powershell
python -m unittest discover -s tests -v
```

Debe terminar:

```text
Ran 201 tests
OK
```

## Aceptación local obligatoria

### 1. Crear configuración local nueva

```powershell
Remove-Item debug_fase15_local_final -Recurse -Force -ErrorAction SilentlyContinue

python nettwin.py pilot-config `
  --output debug_fase15_local_final\pilot_local.json `
  --local-acceptance

$LASTEXITCODE
```

Debe seleccionar una interfaz local física cuando sea posible y escribir una configuración con:

```text
mode = local_acceptance
duration = 12 s
interval = 2 s
target = 127.0.0.1
count = 1
payload = 32 bytes
external traffic = NO
exit code = 0
```

### 2. Validar sin probes

```powershell
python nettwin.py pilot-validate `
  --config debug_fase15_local_final\pilot_local.json

$LASTEXITCODE
```

Esperado:

```text
Estado: PASS
ciclos probes: 6
ICMP echo requests: 6
filas esperadas totales: 18
exit code = 0
```

### 3. Ejecutar pipeline completo

```powershell
python nettwin.py pilot-run `
  --config debug_fase15_local_final\pilot_local.json

$LASTEXITCODE
```

No se usa `--authorized` en modo local.

Esperado:

```text
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
exit code = 0
```

### 4. Verificar resumen — bloque autocontenido

No asumir que `$s` ya existe. Cargarlo siempre:

```powershell
$s = Get-Content `
  debug_fase15_local_final\pilot_summary.json `
  -Encoding UTF8 | ConvertFrom-Json

$s |
Select-Object schema_version,
              phase,
              mode,
              status,
              failed_stage,
              run_id,
              software_version,
              automatic_execution,
              authorization_gate_passed |
Format-List

$s.stages | Format-List
$s.validation.estimate | ConvertTo-Json -Depth 10
$s.integrity_preservation | Format-List
$s.report | ConvertTo-Json -Depth 10
```

Debe mostrar:

```text
software_version = 0.3.0
status = PASS
mode = local_acceptance
authorization_gate_passed = true

validation = PASS
preflight_capture = PASS
integrity_before_report = PASS
analysis_report = PASS
integrity_after_report = PASS

valid_after_report = true
sealed_run_unchanged = true
checksums_sha256_before == checksums_sha256_after
```

### 5. Verificar captura

```powershell
$i = Import-Csv debug_fase15_local_final\run\interface_samples.csv
$h = Import-Csv debug_fase15_local_final\run\host_samples.csv
$p = Import-Csv debug_fase15_local_final\run\probe_samples.csv

"INTERFACE = $($i.Count)"
"HOST      = $($h.Count)"
"PROBES    = $($p.Count)"
```

Esperado aproximadamente para 12 s / 2 s:

```text
INTERFACE = 6
HOST      = 6
PROBES    = 6
```

Todas las filas de probe deben apuntar exclusivamente a `127.0.0.1`:

```powershell
$p |
Select-Object target,address,packets_sent,packets_received,packet_loss_pct,reachability,rtt_ms,sample_status |
Format-Table -AutoSize

$p |
Select-Object -ExpandProperty address |
Sort-Object -Unique
```

**No usar `$p.address`**: en algunas versiones de Windows PowerShell puede colisionar con miembros del objeto y mostrar `OverloadDefinitions`. El segundo comando debe devolver únicamente:

```text
127.0.0.1
```

### 6. Verificar reporte

Deben existir:

```text
report/dataset_quality.json
report/dataset_quality_summary.csv
report/analysis.json
report/report.json
report/report.html
report/analysis/quality_summary.json
report/analysis/events.json
report/analysis/correlations.json
report/analysis/evidence_records.json
report/analysis/event_fingerprints.json
```

Un run corto puede tener cero eventos. No es fallo si los artefactos analíticos existen y el pipeline se ejecutó.

Comprobar metadata:

```powershell
$r = Get-Content `
  debug_fase15_local_final\report\report.json `
  -Encoding UTF8 | ConvertFrom-Json

$r | Select-Object run_id,sensor_version,analysis_executed | Format-List
```

Esperado:

```text
sensor_version = 0.3.0
analysis_executed = True
```

### 7. Integridad independiente

```powershell
python nettwin.py verify-integrity `
  --run-dir debug_fase15_local_final\run `
  --strict-untracked
```

Debe devolver:

```text
Integridad: VÁLIDA
Faltantes: 0
Modificados: 0
Líneas malformadas: 0
Rutas inseguras: 0
No rastreados: 0
```

## Plantilla HacheNet

Crear siempre en una ruta explícita:

```powershell
Remove-Item debug_fase15_template_final -Recurse -Force -ErrorAction SilentlyContinue

python nettwin.py pilot-config `
  --output debug_fase15_template_final\hachenet_pilot.json
```

La plantilla debe indicar que es NO EJECUTABLE. No contiene targets reales inventados.

Validar usando **la misma ruta exacta**:

```powershell
python nettwin.py pilot-validate `
  --config debug_fase15_template_final\hachenet_pilot.json

$LASTEXITCODE
```

DEBE fallar y devolver exit code `2` porque los placeholders y autorización incompleta no son ejecutables. Ese FAIL es un criterio de seguridad correcto.

No ejecutar `pilot-run` sobre esta plantilla.

## Validación productiva antes de la ventana real

Una configuración HacheNet solo puede obtener PASS si:

- `run.duration_seconds = 14400`;
- `run_id` comienza por `HACHENET-`;
- existe 1–3 targets;
- todos los targets fueron proporcionados/aprobados por HacheNet;
- cada target declara `authorized=true`;
- ningún target usa loopback, multicast, link-local o redes de documentación;
- cada target tiene role técnico explícito;
- `pilot.authorization.confirmed=true`;
- `pilot.authorization.reference` está completo;
- `pilot.authorization.approved_by` está completo;
- intervalo de probes >= 2 s;
- payload <= 128 bytes;
- count_per_target <= 3;
- interfaz explícita;
- capacidad solo se informa si se conoce realmente.

## Gate de ejecución real

Incluso con JSON productivo válido, este comando SIN flag:

```powershell
python nettwin.py pilot-run `
  --config RUTA_REAL_DE_CONFIG_HACHENET.json
```

debe quedar bloqueado en `authorization_gate`.

Solo después de aprobación real, revisión final de la configuración y targets explícitos se permite:

```powershell
python nettwin.py pilot-run `
  --config RUTA_REAL_DE_CONFIG_HACHENET.json `
  --authorized
```

## Cierre de software/readiness de Fase 15

```text
11/11 Pilot Engine                 ✅
2/2 CLI                            ✅
201/201 suite                      ✅
version 0.3.0                      ✅
aceptación local loopback PASS     ✅
integridad antes/después PASS      ✅
run sellado sin cambios            ✅
plantilla productiva bloqueada     ✅
doble gate autorización            ✅
```

## Cierre productivo de Fase 15

No declarar la Fase 15 productiva cerrada hasta disponer de:

```text
interfaz HacheNet confirmada        ✅
1–3 targets explícitos autorizados  ✅
roles de targets documentados       ✅
referencia de autorización          ✅
approved_by                         ✅
ventana real = 14400 s              ✅
pilot-run --authorized exit 0       ✅
run real sellado                    ✅
Quality Gate                        PASS/WARN apto
report real generado                ✅
integridad posterior                ✅
```

La ejecución productiva de cuatro horas no debe hacerse hasta disponer de los targets y autorización reales de HacheNet.
