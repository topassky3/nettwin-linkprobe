# NetTwin LinkProbe v0.3 — Piloto HacheNet (Fase 15)

## Objetivo

Fase 15 convierte el pipeline validado en Fases 0–14 en un flujo operativo para el piloto de un enlace HacheNet durante cuatro horas.

El flujo es:

```text
configuración explícita y autorizada
        ↓
pilot-validate (sin probes)
        ↓
doble gate de autorización
        ↓
Preflight
        ↓
Interface + Host + Active Probes
        ↓
run sellado SHA-256
        ↓
verify-integrity --strict
        ↓
Quality / Events / Correlations / Evidence / Fingerprints
        ↓
Link Health Audit
        ↓
verify-integrity --strict nuevamente
        ↓
pilot_summary.json
```

## Dos modos separados

Fase 15 tiene dos modos que nunca deben confundirse:

- `local_acceptance`: valida software/readiness usando exclusivamente `127.0.0.1`.
- `hachenet_pilot`: corresponde a la ventana productiva real de 4 h sobre targets explícitos y autorizados por HacheNet.

Una aceptación local exitosa no equivale a haber ejecutado el piloto productivo.

## Principio de seguridad

Fase 15 no inventa targets ni descubre la red.

La plantilla de producción generada por `pilot-config` es deliberadamente NO ejecutable. El administrador debe completar:

- interfaz autorizada;
- uno a tres targets previamente aprobados;
- role técnico de cada target;
- `authorized=true` por target;
- referencia de aprobación;
- responsable que aprobó los targets;
- capacidad del enlace únicamente si se conoce de forma explícita.

No se acepta en modo productivo:

- loopback;
- multicast;
- link-local;
- direcciones de documentación RFC (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32`);
- más de tres targets;
- payload ICMP mayor de 128 bytes;
- más de tres echoes por target/ciclo;
- intervalo de probes menor de 2 s;
- duración distinta de 14400 s.

El código no implementa captura de payload, inspección de comunicaciones, descubrimiento automático, escaneo ni pruebas agresivas de throughput.

## Doble gate de autorización

Para modo `hachenet_pilot` deben cumplirse simultáneamente:

1. En el JSON:

```json
"authorization": {
  "confirmed": true,
  "reference": "REFERENCIA_REAL_DE_APROBACION",
  "approved_by": "RESPONSABLE_REAL"
}
```

2. En el comando:

```text
--authorized
```

Si falta cualquiera de los dos, los collectors no deben empezar.

## Crear plantilla productiva

```powershell
python nettwin.py pilot-config `
  --output hachenet_pilot.json
```

La plantilla contiene placeholders y `authorization.confirmed=false`. Debe fallar `pilot-validate` hasta ser completada con información real aprobada.

## Validación estática

```powershell
python nettwin.py pilot-validate `
  --config hachenet_pilot.json
```

Esta operación:

- no ejecuta probes;
- no resuelve targets;
- no modifica red;
- valida ventana, carga, límites y autorización;
- estima filas y payload ICMP saliente.

## Ejecución productiva

Solo después de aprobación real:

```powershell
python nettwin.py pilot-run `
  --config hachenet_pilot.json `
  --authorized
```

No usar este comando con placeholders.

## Aceptación local

Para validar el código antes de HacheNet existe un modo separado:

```powershell
python nettwin.py pilot-config `
  --output debug_fase15_local\pilot_local.json `
  --local-acceptance
```

Ese modo:

- dura 12 s por defecto;
- utiliza solo `127.0.0.1`;
- no requiere `--authorized` porque no sale del propio host;
- conserva el mismo pipeline de preflight, captura, integridad, análisis e informe.

## Artefactos

Al completar una ejecución:

```text
pilot_summary.json
run/
  experiment_config.json
  preflight.json
  interface_samples.csv
  host_samples.csv
  probe_samples.csv
  orchestration.json
  run_metadata.json
  checksums.sha256
  integrity_report.json
  logs/linkprobe.log

report/
  dataset_quality.json
  dataset_quality_summary.csv
  analysis.json
  report.json
  report.html
  analysis/...
```

El informe se genera fuera del run sellado. La integridad se verifica antes y después del análisis y el SHA-256 de `checksums.sha256` debe permanecer idéntico.

## Interpretación

Un piloto de cuatro horas caracteriza únicamente esa ventana. No representa SLA histórico, comportamiento semanal, crecimiento mensual, saturación futura ni causa física definitiva.

Los hallazgos conservan la secuencia:

```text
HECHO
↓
INTERPRETACIÓN
↓
HIPÓTESIS
↓
CONFIANZA
↓
RECOMENDACIÓN
```

Correlación no implica causalidad.

## Cierre

### Software/readiness

Antes de preparar la ventana real deben cumplirse:

```text
11/11 Pilot Engine                 ✅
2/2 CLI                            ✅
201/201 suite                      ✅
NetTwin 0.3.0                      ✅
local_acceptance PASS              ✅
run íntegro                        ✅
reporte generado                   ✅
plantilla productiva bloqueada     ✅
doble authorization gate          ✅
```

### Piloto productivo

Fase 15 productiva solo queda cerrada cuando también existen:

```text
interfaz HacheNet confirmada        ✅
1–3 targets explícitos autorizados  ✅
roles documentados                  ✅
referencia de aprobación            ✅
approved_by                         ✅
pilot-validate PASS                 ✅
ventana real 14400 s                ✅
pilot-run --authorized exit 0       ✅
Quality Gate apto                   ✅
run real sellado                    ✅
report real generado                ✅
integridad posterior válida         ✅
```
