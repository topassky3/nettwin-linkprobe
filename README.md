# NetTwin LinkProbe v0.3

NetTwin LinkProbe es un sensor y pipeline de análisis para caracterizar la calidad de un enlace durante una **ventana observada**, preservar la evidencia y generar automáticamente un informe técnico reproducible.

Versión actual: **NetTwin ISP 0.3.0**.

> ## Idea principal
>
> El técnico **NO debe abrir ni analizar manualmente los CSV**.
>
> En el piloto real, el técnico:
>
> 1. instala NetTwin;
> 2. define los datos autorizados del enlace;
> 3. valida la configuración;
> 4. ejecuta `pilot-run` una sola vez;
> 5. deja el proceso funcionando durante 4 horas;
> 6. al finalizar, NetTwin analiza automáticamente la captura y genera el informe.
>
> Los CSV quedan como **evidencia sellada**, no como trabajo manual para el operador.

---

# 1. Qué hace automáticamente NetTwin

Una vez iniciado `pilot-run`, el proceso completo es:

```text
validación de configuración
        |
        v
gate de autorización
        |
        v
preflight
        |
        v
4 HORAS DE CAPTURA COORDINADA
        |
        +--> Interface Collector
        +--> Host Collector
        +--> Active Probe Engine
        |
        v
sellado SHA-256
        |
        v
verificación estricta de integridad
        |
        v
Quality Gate
        |
        v
Event Engine
        |
        v
Correlation Engine
        |
        v
Evidence Engine
        |
        v
Event Fingerprints
        |
        v
Link Health Audit
        |
        v
segunda verificación de integridad
        |
        v
pilot_summary.json + report/report.html
```

**No hay que ejecutar manualmente los motores después de las 4 horas.**

---

# 2. Qué datos registra durante las 4 horas

NetTwin no captura contenido de usuarios ni payload de comunicaciones.

Registra métricas técnicas.

## Interfaz observada

En `run/interface_samples.csv` quedan, entre otras:

```text
rx_bytes
tx_bytes
rx_packets
tx_packets
rx_errors
tx_errors
rx_drops
tx_drops
rx_bytes_delta
tx_bytes_delta
```

## Servidor

En `run/host_samples.csv` quedan métricas de salud del host, como CPU y RAM.

## Targets autorizados

En `run/probe_samples.csv` quedan resultados de las sondas configuradas: reachability, pérdida y RTT cuando el sistema operativo permite medirlo de forma fiable.

Estos archivos se conservan para trazabilidad. **El técnico no tiene que interpretarlos manualmente.**

---

# 3. Instalación

## Windows / PowerShell

```powershell
git clone https://github.com/topassky3/nettwin-linkprobe.git
cd nettwin-linkprobe
python -m pip install -r requirements.txt
python nettwin.py --version
```

Debe mostrar:

```text
NetTwin ISP 0.3.0
```

Si el repositorio ya existe:

```powershell
git switch main
git pull origin main
python -m pip install -r requirements.txt
python nettwin.py --version
```

---

# 4. Prueba local rápida antes del piloto

Esta prueba dura 12 segundos, usa únicamente `127.0.0.1` y no genera tráfico activo externo.

```powershell
Remove-Item prueba_nettwin_local -Recurse -Force -ErrorAction SilentlyContinue

python nettwin.py pilot-config `
  --output prueba_nettwin_local\pilot_local.json `
  --local-acceptance

python nettwin.py pilot-validate `
  --config prueba_nettwin_local\pilot_local.json

python nettwin.py pilot-run `
  --config prueba_nettwin_local\pilot_local.json
```

Resultado esperado:

```text
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
```

Abrir el informe:

```powershell
Start-Process (
  Resolve-Path prueba_nettwin_local\report\report.html
)
```

Si esta prueba pasa, la instalación y el pipeline local están funcionando.

---

# 5. Piloto real HacheNet — flujo recomendado para el técnico

El piloto productivo dura exactamente:

```text
14400 segundos = 4 horas
```

Antes de comenzar, HacheNet debe proporcionar datos **reales y autorizados**:

- interfaz del servidor que corresponde al enlace a observar;
- entre 1 y 3 targets autorizados;
- rol técnico de cada target;
- responsable que autorizó los targets;
- referencia verificable de autorización;
- capacidad real del enlace, solo si se conoce.

No inventar estos datos.

---

# 6. Ejemplo operativo con UN target autorizado

El técnico puede hacer todo desde PowerShell.

## Paso 1 — definir únicamente los datos reales

**Cambiar los valores de este bloque por los datos aprobados por HacheNet:**

```powershell
$RUN_ID       = "HACHENET-20260810-LINK01-001"
$INTERFACE    = "REEMPLAZAR_POR_INTERFAZ_REAL"
$TARGET_NAME  = "REEMPLAZAR_POR_NOMBRE_TARGET"
$TARGET_IP    = "REEMPLAZAR_POR_IP_AUTORIZADA"
$TARGET_ROLE  = "REEMPLAZAR_POR_ROL_TECNICO"
$APPROVED_BY  = "REEMPLAZAR_POR_RESPONSABLE"
$AUTH_REF     = "REEMPLAZAR_POR_REFERENCIA_AUTORIZACION"

# Si la capacidad real del enlace es conocida, ejemplo: 300
# Si no se conoce, dejar $null.
$CAPACITY_MBPS = $null

$PILOT_DIR    = "pilotos\$RUN_ID"
$PILOT_CONFIG = "$PILOT_DIR\hachenet_pilot.json"
```

Ejemplos válidos de interfaz podrían ser `Ethernet` o el nombre exacto que muestre:

```powershell
python nettwin.py interfaces
```

La IP del target debe ser una dirección que HacheNet haya autorizado explícitamente.

---

## Paso 2 — crear la carpeta y la plantilla

```powershell
New-Item -ItemType Directory -Path $PILOT_DIR -Force | Out-Null

python nettwin.py pilot-config `
  --output $PILOT_CONFIG `
  --interface $INTERFACE `
  --run-id $RUN_ID
```

La salida inicial dirá:

```text
Estado: PLANTILLA NO EJECUTABLE
```

Eso es normal: todavía faltan target y autorización.

---

## Paso 3 — completar la plantilla por PowerShell

No es necesario abrir el JSON en un editor.

```powershell
$c = Get-Content $PILOT_CONFIG -Encoding UTF8 | ConvertFrom-Json

$c.run.run_id = $RUN_ID
$c.run.duration_seconds = 14400

$c.interface.name = $INTERFACE
$c.interface.capacity_mbps = $CAPACITY_MBPS

$c.targets[0].name = $TARGET_NAME
$c.targets[0].address = $TARGET_IP
$c.targets[0].role = $TARGET_ROLE
$c.targets[0].authorized = $true

$c.pilot.authorization.confirmed = $true
$c.pilot.authorization.reference = $AUTH_REF
$c.pilot.authorization.approved_by = $APPROVED_BY

$c | ConvertTo-Json -Depth 20 | Set-Content $PILOT_CONFIG -Encoding UTF8
```

---

## Paso 4 — validar SIN iniciar las 4 horas

```powershell
python nettwin.py pilot-validate `
  --config $PILOT_CONFIG

$LASTEXITCODE
```

Antes de continuar debe aparecer:

```text
Modo: hachenet_pilot
Estado: PASS
```

Y el código de salida debe ser:

```text
0
```

El validador también muestra:

- duración;
- cantidad de targets;
- número estimado de sondas;
- carga ICMP estimada;
- cantidad esperada de filas;
- advertencias.

`pilot-validate` **no ejecuta probes y no modifica la red**.

Si devuelve `FAIL`, no iniciar el piloto.

---

# 7. Ejecutar las 4 horas

Solo después de obtener `PASS` y verificar que interfaz/target/autorización sean correctos:

```powershell
python nettwin.py pilot-run `
  --config $PILOT_CONFIG `
  --authorized
```

## Qué hace el técnico después de ejecutar ese comando

**Nada durante la ventana.**

El proceso permanece ejecutándose y NetTwin realiza automáticamente la captura durante las 4 horas.

Durante ese tiempo:

- no cerrar la terminal;
- no apagar ni reiniciar el servidor;
- no suspender el equipo;
- no modificar los archivos del directorio del piloto;
- no volver a ejecutar `pilot-run` sobre el mismo workspace.

Cuando transcurran las 4 horas, el mismo comando continúa automáticamente con:

```text
sellado
-> integridad
-> Quality Gate
-> eventos
-> correlaciones
-> Evidence Engine
-> fingerprints
-> informe
-> segunda verificación
```

**No hay que entrar a los CSV ni ejecutar otro comando de análisis.**

---

# 8. Qué debe aparecer al final

El comando debe terminar mostrando algo equivalente a:

```text
Fase 15 finalizada.
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
```

Y:

```powershell
$LASTEXITCODE
```

Debe devolver:

```text
0
```

---

# 9. Dónde queda el resultado

Todo queda dentro de la carpeta creada para ese `RUN_ID`.

Ejemplo:

```text
pilotos/
└── HACHENET-20260810-LINK01-001/
    ├── hachenet_pilot.json
    ├── pilot_summary.json
    │
    ├── run/
    │   ├── experiment_config.json
    │   ├── preflight.json
    │   ├── interface_samples.csv
    │   ├── host_samples.csv
    │   ├── probe_samples.csv
    │   ├── orchestration.json
    │   ├── run_metadata.json
    │   ├── checksums.sha256
    │   └── logs/
    │       └── linkprobe.log
    │
    └── report/
        ├── dataset_quality.json
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

---

# 10. Qué archivos mira el técnico

El técnico solo necesita revisar dos resultados:

## A. Estado general

```text
pilot_summary.json
```

Debe indicar:

```text
status = PASS
validation = PASS
preflight_capture = PASS
integrity_before_report = PASS
analysis_report = PASS
integrity_after_report = PASS
```

## B. Informe técnico

```text
report\report.html
```

Abrirlo con:

```powershell
Start-Process (
  Resolve-Path "$PILOT_DIR\report\report.html"
)
```

**No hace falta abrir `interface_samples.csv`, `host_samples.csv` o `probe_samples.csv` para obtener el resultado del piloto.**

Los CSV son evidencia original para auditoría, reproducción y análisis posterior si alguna vez se necesita profundizar.

---

# 11. Qué entrega HacheNet

El administrador debe conservar y entregar la carpeta completa:

```text
pilotos\HACHENET-...\
```

No editar manualmente los archivos dentro de `run/`.

La carpeta completa contiene:

1. configuración exacta utilizada;
2. evidencia cruda;
3. hashes SHA-256;
4. metadata del run;
5. análisis automático;
6. informe final.

---

# 12. Verificación final opcional

Si el técnico quiere comprobar la evidencia antes de entregar:

```powershell
python nettwin.py verify-integrity `
  --run-dir "$PILOT_DIR\run" `
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

Esto es una verificación adicional. El propio `pilot-run` ya realiza verificaciones de integridad antes y después del análisis.

---

# 13. Qué significa el informe

NetTwin describe únicamente la ventana observada.

No convierte automáticamente:

- 4 horas en SLA histórico;
- 4 horas en comportamiento semanal o mensual;
- correlación en causalidad;
- velocidad de NIC en capacidad contratada;
- ausencia de eventos en prueba de ausencia histórica de degradación.

Los hallazgos se organizan como:

```text
HECHO
-> INTERPRETACIÓN
-> HIPÓTESIS
-> CONFIANZA
-> RECOMENDACIÓN
```

Cuando una métrica no puede medirse de forma fiable, NetTwin usa `N/D` en lugar de inventar un valor.

---

# 14. Seguridad por diseño

NetTwin no:

- captura payload de clientes;
- inspecciona conversaciones o navegación;
- escanea rangos de red;
- descubre hosts automáticamente;
- modifica firewall;
- modifica routing;
- modifica interfaces;
- modifica servicios;
- ejecuta throughput agresivo.

En modo productivo, el validador limita además:

- máximo 3 targets;
- targets con `authorized=true`;
- duración exactamente 4 horas;
- intervalo de probes >= 2 s;
- payload ICMP <= 128 bytes;
- máximo 3 echoes por target/ciclo;
- rechazo de loopback, multicast, link-local y redes de documentación.

---

# 15. Flujo resumido para el operador

```text
CLONAR / ACTUALIZAR
        |
        v
INSTALAR DEPENDENCIAS
        |
        v
DEFINIR DATOS AUTORIZADOS
        |
        v
CREAR pilot config
        |
        v
pilot-validate
        |
        +---- FAIL -> corregir configuración, NO ejecutar
        |
       PASS
        |
        v
pilot-run --authorized
        |
        v
        4 HORAS
        |
        v
ANÁLISIS AUTOMÁTICO
        |
        v
pilot_summary.json
        +
report/report.html
        |
        v
ENTREGAR CARPETA COMPLETA
```

**El técnico no tiene que analizar CSVs manualmente.**

---

# 16. Documentación técnica

Operación del piloto:

- `docs/GUIA_CLIENTE_HACHENET.md`
- `docs/PILOT_HACHENET.md`

Arquitectura:

- `docs/PREFLIGHT.md`
- `docs/ORCHESTRATOR.md`
- `docs/INTEGRITY_REPRODUCIBILITY.md`
- `docs/QUALITY_ENGINE.md`
- `docs/EVENT_ENGINE.md`
- `docs/CORRELATION_ENGINE.md`
- `docs/EVIDENCE_ENGINE.md`
- `docs/FINGERPRINT_ENGINE.md`
- `docs/REPORTING.md`

---

# Estado validado de la release

```text
NetTwin ISP 0.3.0
201/201 tests
Dry-run integral: PASS
Aceptación local Fase 15: PASS
```
