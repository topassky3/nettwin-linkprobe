# NetTwin LinkProbe v0.3

NetTwin LinkProbe es un sensor y pipeline de análisis para caracterizar la calidad de un enlace de red durante una **ventana observada**, preservar la evidencia de la captura y producir un informe técnico reproducible.

La versión actual es **NetTwin ISP 0.3.0**.

> **Importante:** este README separa dos flujos distintos:
>
> 1. **Prueba local segura** — funciona inmediatamente y debe terminar en `PASS`.
> 2. **Piloto real HacheNet** — la plantilla inicial está deliberadamente bloqueada y **debe dar `FAIL`** hasta que se completen interfaz, targets y autorización reales.

---

## Estado del proyecto

- Versión: `0.3.0`
- Suite validada: `201/201`
- Dry-run integral: `PASS`
- Aceptación local Fase 15: `PASS`
- Integridad SHA-256 antes/después del reporte: validada
- Piloto productivo HacheNet: pendiente de datos y autorización reales

---

## Qué hace

```text
Configuración explícita
        |
        v
Preflight
        |
        v
Interface Collector + Host Collector + Active Probe Engine
        |
        v
Evidencia cruda
        |
        v
Sellado SHA-256
        |
        v
Quality Gate
        |
        v
Event Engine
Correlation Engine
Evidence Engine
Event Fingerprints
        |
        v
Link Health Audit
        |
        v
Verificación de integridad posterior
```

NetTwin separa **hechos, interpretación, hipótesis, confianza y recomendación**. No presenta correlación como causalidad y no sustituye métricas desconocidas con valores inventados.

---

# 1. Instalación

## Windows / PowerShell

```powershell
git clone https://github.com/topassky3/nettwin-linkprobe.git
cd nettwin-linkprobe
python -m pip install -r requirements.txt
python nettwin.py --version
```

Salida esperada:

```text
NetTwin ISP 0.3.0
```

Si ya clonaste el repositorio:

```powershell
git switch main
git pull origin main
python -m pip install -r requirements.txt
python nettwin.py --version
```

---

# 2. Prueba rápida segura — este es el primer flujo que debes ejecutar

Esta prueba sirve para demostrar que el producto funciona **sin utilizar ningún target externo**.

- duración: 12 segundos;
- target único: `127.0.0.1`;
- tráfico activo externo: NO;
- payload capture: NO;
- escaneo o descubrimiento: NO.

## Paso 1 — crear un workspace nuevo

```powershell
Remove-Item prueba_nettwin_local -Recurse -Force -ErrorAction SilentlyContinue
```

## Paso 2 — crear la configuración local

```powershell
python nettwin.py pilot-config `
  --output prueba_nettwin_local\pilot_local.json `
  --local-acceptance
```

Salida esperada:

```text
Modo: local_acceptance
Target: local_loopback -> 127.0.0.1
Tráfico externo: NO
Esta configuración sí puede usarse para aceptación local.
```

Comprueba el código de salida:

```powershell
$LASTEXITCODE
```

Esperado:

```text
0
```

## Paso 3 — validar sin ejecutar probes

```powershell
python nettwin.py pilot-validate `
  --config prueba_nettwin_local\pilot_local.json
```

Salida esperada:

```text
Validación Fase 15
Modo: local_acceptance
Estado: PASS
```

Es normal ver una advertencia como:

```text
interface.capacity_mbps es N/D; utilización quedará N/D
```

Eso **no es un error**. NetTwin no utiliza la velocidad reportada por la NIC como sustituto de la capacidad real del enlace.

Comprueba:

```powershell
$LASTEXITCODE
```

Esperado:

```text
0
```

## Paso 4 — ejecutar el pipeline completo

```powershell
python nettwin.py pilot-run `
  --config prueba_nettwin_local\pilot_local.json
```

**No uses `--authorized` en la aceptación local.**

Salida esperada:

```text
Fase 15 finalizada.
Estado: PASS
Modo: local_acceptance
Payload capture: NO
Escaneo/descubrimiento automático: NO
Quality Gate: PASS
Análisis ejecutado: SÍ
```

Comprueba:

```powershell
$LASTEXITCODE
```

Esperado:

```text
0
```

## Paso 5 — abrir el informe

```powershell
Start-Process (
  Resolve-Path prueba_nettwin_local\report\report.html
)
```

En una ejecución local normal puedes observar, por ejemplo:

```text
Quality Gate: PASS
interface: 6/6
host:      6/6
probes:    6/6
Disponibilidad observada: 100%
Pérdida observada: 0%
```

RTT puede aparecer como `N/D` si el sistema operativo confirma recepción de ICMP pero su salida no permite extraer de forma fiable el RTT. Eso es un comportamiento conservador, no un fallo.

## Paso 6 — verificar la evidencia

```powershell
python nettwin.py verify-integrity `
  --run-dir prueba_nettwin_local\run `
  --strict-untracked
```

Esperado:

```text
Integridad: VÁLIDA
Faltantes: 0
Modificados: 0
Rutas inseguras: 0
No rastreados: 0
```

---

# 3. Comportamientos que parecen errores pero son protecciones correctas

## A. Ejecutar `pilot-run` dos veces sobre el mismo workspace

Si vuelves a ejecutar:

```powershell
python nettwin.py pilot-run `
  --config prueba_nettwin_local\pilot_local.json
```

puedes recibir:

```text
error: El directorio de reporte ya contiene archivos
```

Esto es **esperado**.

NetTwin no sobrescribe una captura o reporte existente porque hacerlo podría mezclar o destruir evidencia.

Para repetir una prueba usa un workspace nuevo:

```powershell
python nettwin.py pilot-config `
  --output prueba_nettwin_local_02\pilot_local.json `
  --local-acceptance
```

## B. Crear la plantilla HacheNet y validarla inmediatamente

Este comando:

```powershell
python nettwin.py pilot-config --output hachenet_pilot.json
```

genera deliberadamente:

```text
Modo: hachenet_pilot
Estado: PLANTILLA NO EJECUTABLE
```

Si inmediatamente haces:

```powershell
python nettwin.py pilot-validate --config hachenet_pilot.json
```

**debe producir `FAIL`.**

Por ejemplo:

```text
Estado: FAIL
Target 'REEMPLAZAR_TARGET_AUTORIZADO' tiene address inválido:
'REEMPLAZAR_IP_AUTORIZADA'
```

Eso significa que el control de seguridad está funcionando. No significa que NetTwin esté roto.

---

# 4. Piloto real HacheNet — NO ejecutar hasta tener datos autorizados

El piloto productivo está diseñado para una ventana de exactamente:

```text
14400 segundos = 4 horas
```

Antes de ejecutar necesitamos que HacheNet defina explícitamente:

1. interfaz real del servidor asociada al enlace que se observará;
2. entre 1 y 3 targets autorizados;
3. rol técnico de cada target;
4. responsable que aprobó los targets;
5. referencia verificable de la autorización;
6. capacidad real del enlace, únicamente si se conoce.

No inventes ninguno de estos datos.

La guía completa para el administrador está en:

```text
docs/GUIA_CLIENTE_HACHENET.md
```

---

## Paso 1 — crear la plantilla productiva

```powershell
python nettwin.py pilot-config --output hachenet_pilot.json
```

Debe mostrar:

```text
Modo: hachenet_pilot
Estado: PLANTILLA NO EJECUTABLE
```

Esto es correcto.

---

## Paso 2 — completar `hachenet_pilot.json`

Los placeholders deben reemplazarse exclusivamente con información real aprobada.

Ejemplo de estructura:

```json
{
  "run": {
    "run_id": "HACHENET-AAAAMMDD-LINK01-001",
    "duration_seconds": 14400
  },
  "interface": {
    "name": "INTERFAZ_REAL_AUTORIZADA",
    "interval_seconds": 5,
    "capacity_mbps": null
  },
  "probes": {
    "interval_seconds": 5,
    "timeout_ms": 1000,
    "payload_bytes": 32,
    "count_per_target": 1
  },
  "targets": [
    {
      "name": "TARGET_APROBADO_1",
      "address": "IP_REAL_AUTORIZADA",
      "role": "ROL_TECNICO_REAL",
      "authorized": true
    }
  ],
  "pilot": {
    "mode": "hachenet_pilot",
    "client": "HacheNet",
    "schema_version": "linkprobe-pilot-v1",
    "authorization": {
      "confirmed": true,
      "reference": "REFERENCIA_REAL_DE_AUTORIZACION",
      "approved_by": "RESPONSABLE_REAL"
    }
  }
}
```

No copies las palabras `INTERFAZ_REAL_AUTORIZADA`, `IP_REAL_AUTORIZADA` o similares como valores reales. Son marcadores explicativos.

---

## Paso 3 — validar antes de ejecutar

```powershell
python nettwin.py pilot-validate --config hachenet_pilot.json
```

Este comando **no ejecuta probes y no modifica la red**.

Antes de continuar debe aparecer:

```text
Modo: hachenet_pilot
Estado: PASS
```

Además revisa:

- duración = `4.000 h`;
- targets = `1–3`;
- carga ICMP estimada;
- filas esperadas;
- advertencias.

Si aparece `FAIL`, **no ejecutes `pilot-run`**. Corrige primero la configuración.

---

## Paso 4 — ejecutar el piloto productivo

Solo después de obtener `PASS` y confirmar la autorización:

```powershell
python nettwin.py pilot-run `
  --config hachenet_pilot.json `
  --authorized
```

El flag `--authorized` es un segundo gate deliberado. No sustituye la autorización escrita dentro del JSON; ambos deben existir.

Una vez iniciado, el proceso es automático:

```text
validation
-> authorization gate
-> preflight
-> capture
-> SHA-256 seal
-> strict integrity verification
-> Quality Gate
-> Events / Correlations / Evidence / Fingerprints
-> report
-> strict integrity verification again
-> pilot_summary.json
```

---

# 5. Qué archivos genera una ejecución correcta

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
  logs/
    linkprobe.log
report/
  dataset_quality.json
  dataset_quality_summary.csv
  analysis.json
  report.json
  report.html
  analysis/
    quality_summary.json
    events.json
    correlations.json
    evidence_records.json
    event_fingerprints.json
    ...
```

El directorio `run/` queda sellado. El reporte se genera fuera del run para preservar los datos originales.

---

# 6. Cómo interpretar el informe

NetTwin describe únicamente la **ventana observada**.

No interpreta automáticamente:

- disponibilidad observada como SLA histórico;
- una ventana de 4 horas como comportamiento semanal o mensual;
- correlación como causalidad;
- velocidad de la NIC como capacidad real del enlace;
- ausencia de eventos como prueba de ausencia histórica de problemas.

Los hallazgos siguen esta estructura:

```text
HECHO
-> INTERPRETACIÓN
-> HIPÓTESIS
-> CONFIANZA
-> RECOMENDACIÓN
```

Si una métrica no puede medirse de forma fiable, NetTwin muestra `N/D` en lugar de inventarla.

---

# 7. Seguridad por diseño

NetTwin no:

- captura payload de clientes;
- inspecciona conversaciones, navegación o contenido de usuarios;
- escanea rangos de red;
- descubre hosts automáticamente;
- modifica firewall;
- modifica routing;
- modifica interfaces;
- modifica servicios;
- ejecuta throughput agresivo.

En modo productivo, además, el validador limita:

- máximo 3 targets;
- targets explícitamente marcados `authorized=true`;
- intervalo de probes >= 2 s;
- payload ICMP <= 128 bytes;
- count por target/ciclo <= 3;
- duración exactamente 4 horas;
- rechazo de loopback, multicast, link-local y redes reservadas para documentación.

---

# 8. Comandos útiles

Ver versión:

```powershell
python nettwin.py --version
```

Ver comandos disponibles:

```powershell
python nettwin.py --help
```

Listar interfaces locales:

```powershell
python nettwin.py interfaces
```

Validar una configuración productiva sin ejecutar probes:

```powershell
python nettwin.py pilot-validate --config hachenet_pilot.json
```

Verificar integridad de un run:

```powershell
python nettwin.py verify-integrity --run-dir run --strict-untracked
```

Ejecutar la suite de desarrollo:

```powershell
python -m unittest discover -s tests -v
```

Referencia validada de la release:

```text
Ran 201 tests
OK
```

---

# 9. Documentación

Para operar el piloto:

- `docs/GUIA_CLIENTE_HACHENET.md`
- `docs/PILOT_HACHENET.md`

Arquitectura y motores:

- `docs/PREFLIGHT.md`
- `docs/ORCHESTRATOR.md`
- `docs/INTEGRITY_REPRODUCIBILITY.md`
- `docs/QUALITY_ENGINE.md`
- `docs/EVENT_ENGINE.md`
- `docs/CORRELATION_ENGINE.md`
- `docs/EVIDENCE_ENGINE.md`
- `docs/FINGERPRINT_ENGINE.md`
- `docs/REPORTING.md`

Aceptaciones:

- `docs/PHASE13_ACCEPTANCE.md`
- `docs/PHASE14_ACCEPTANCE.md`
- `docs/PHASE15_ACCEPTANCE.md`

---

# 10. Ruta recomendada

Para alguien que acaba de clonar NetTwin:

```text
git clone
   |
   v
install requirements
   |
   v
--local-acceptance
   |
   v
pilot-validate -> PASS
   |
   v
pilot-run -> PASS
   |
   v
abrir report.html
```

Para HacheNet:

```text
pilot-config
   |
   v
PLANTILLA NO EJECUTABLE
   |
   v
completar datos reales autorizados
   |
   v
pilot-validate
   |
   +---- FAIL -> corregir y NO ejecutar
   |
   v
PASS
   |
   v
pilot-run --authorized
   |
   v
4 horas
   |
   v
run/ + report/ + pilot_summary.json
```

Ese es el flujo correcto de NetTwin LinkProbe v0.3.0.
