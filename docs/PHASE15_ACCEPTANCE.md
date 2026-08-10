# Fase 15 — Criterios de aceptación

## Alcance

Fase 15 prepara NetTwin LinkProbe v0.3 para el piloto real de HacheNet de cuatro horas y proporciona una aceptación local separada que no envía tráfico fuera del host.

## Tests automáticos

Fase 14 cerró con 188 pruebas. Fase 15 añade 13:

```text
11/11 Pilot Engine
2/2 CLI público
201/201 suite completa
```

No cerrar Fase 15 si existe cualquier FAIL o ERROR.

## Aceptación local obligatoria

### 1. Crear configuración local

```powershell
python nettwin.py pilot-config `
  --output debug_fase15_local_01/pilot_local.json `
  --local-acceptance
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
```

### 2. Validar sin probes

```powershell
python nettwin.py pilot-validate `
  --config debug_fase15_local_01/pilot_local.json
```

Esperado:

```text
Estado: PASS
ciclos probes: 6
ICMP echo requests: 6
filas esperadas totales: 18
```

### 3. Ejecutar pipeline completo

```powershell
python nettwin.py pilot-run `
  --config debug_fase15_local_01/pilot_local.json
```

No se usa `--authorized` en modo local.

Esperado:

```text
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
```

### 4. Verificar resumen

`pilot_summary.json` debe mostrar:

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
```

### 5. Verificar captura

Esperado aproximadamente para 12 s / 2 s:

```text
interface = 6 filas
host = 6 filas
probes = 6 filas
```

Todas las filas de probe deben apuntar exclusivamente a `127.0.0.1`.

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

### 7. Integridad posterior

`pilot_summary.json` debe indicar:

```text
valid_after_report = true
sealed_run_unchanged = true
checksums_sha256_before == checksums_sha256_after
```

Y además:

```powershell
python nettwin.py verify-integrity `
  --run-dir debug_fase15_local_01/run `
  --strict-untracked
```

Debe devolver integridad válida y cero archivos faltantes, modificados o no rastreados.

## Plantilla HacheNet

Crear:

```powershell
python nettwin.py pilot-config `
  --output hachenet_pilot_template.json
```

Inmediatamente después:

```powershell
python nettwin.py pilot-validate `
  --config hachenet_pilot_template.json
```

DEBE fallar porque los placeholders y autorización incompleta no son ejecutables. Ese FAIL es un criterio de seguridad correcto.

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

Incluso con JSON válido, este comando SIN flag:

```powershell
python nettwin.py pilot-run --config hachenet_pilot.json
```

debe quedar bloqueado en `authorization_gate`.

Solo después de aprobación real se permite:

```powershell
python nettwin.py pilot-run `
  --config hachenet_pilot.json `
  --authorized
```

## Cierre de Fase 15

Para considerar la implementación lista antes de la ventana real deben cumplirse:

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

La ejecución productiva de cuatro horas no debe hacerse hasta disponer de los targets y autorización reales de HacheNet.
