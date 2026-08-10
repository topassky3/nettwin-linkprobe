# Guía del cliente — Piloto HacheNet de NetTwin LinkProbe v0.3

Esta guía está pensada para el técnico o administrador que ejecutará el piloto real.

## Regla principal

El operador **no tiene que abrir, leer ni analizar manualmente los CSV**.

Su trabajo es:

```text
preparar configuración autorizada
-> validar
-> ejecutar pilot-run
-> dejar correr 4 horas
-> revisar PASS
-> abrir report/report.html
-> entregar la carpeta completa
```

NetTwin realiza automáticamente después de la captura:

```text
sellado SHA-256
-> verificación de integridad
-> Quality Gate
-> Event Engine
-> Correlation Engine
-> Evidence Engine
-> Event Fingerprints
-> Link Health Audit
-> segunda verificación de integridad
```

---

# 1. Datos que HacheNet debe definir antes de ejecutar

Se requieren datos reales y autorizados:

1. interfaz del servidor correspondiente al enlace a observar;
2. entre 1 y 3 targets autorizados para ICMP;
3. rol técnico de cada target;
4. responsable que aprobó los targets;
5. referencia verificable de autorización;
6. capacidad real del enlace únicamente si se conoce.

Si la capacidad no se conoce, se deja `null`. NetTwin no utiliza la velocidad de la NIC como sustituto.

No se requieren credenciales ni contenido de comunicaciones de clientes.

---

# 2. Instalar NetTwin

En PowerShell:

```powershell
git clone https://github.com/topassky3/nettwin-linkprobe.git
cd nettwin-linkprobe
python -m pip install -r requirements.txt
python nettwin.py --version
```

Esperado:

```text
NetTwin ISP 0.3.0
```

---

# 3. Definir los datos reales del piloto

El técnico cambia únicamente estos valores:

```powershell
$RUN_ID       = "HACHENET-20260810-LINK01-001"
$INTERFACE    = "REEMPLAZAR_POR_INTERFAZ_REAL"
$TARGET_NAME  = "REEMPLAZAR_POR_NOMBRE_TARGET"
$TARGET_IP    = "REEMPLAZAR_POR_IP_AUTORIZADA"
$TARGET_ROLE  = "REEMPLAZAR_POR_ROL_TECNICO"
$APPROVED_BY  = "REEMPLAZAR_POR_RESPONSABLE"
$AUTH_REF     = "REEMPLAZAR_POR_REFERENCIA_AUTORIZACION"

# Ejemplo si la capacidad real es conocida: 300
# Si no se conoce:
$CAPACITY_MBPS = $null

$PILOT_DIR    = "pilotos\$RUN_ID"
$PILOT_CONFIG = "$PILOT_DIR\hachenet_pilot.json"
```

Para listar interfaces visibles:

```powershell
python nettwin.py interfaces
```

---

# 4. Crear configuración

```powershell
New-Item -ItemType Directory -Path $PILOT_DIR -Force | Out-Null

python nettwin.py pilot-config `
  --output $PILOT_CONFIG `
  --interface $INTERFACE `
  --run-id $RUN_ID
```

La plantilla inicialmente mostrará:

```text
Estado: PLANTILLA NO EJECUTABLE
```

Eso es esperado porque todavía falta completar target y autorización.

---

# 5. Completar la configuración por comando

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

No es necesario editar el JSON manualmente.

---

# 6. Validar antes de ejecutar

```powershell
python nettwin.py pilot-validate `
  --config $PILOT_CONFIG

$LASTEXITCODE
```

Debe aparecer:

```text
Modo: hachenet_pilot
Estado: PASS
```

Y el código de salida debe ser:

```text
0
```

El validador muestra además la duración, targets, cantidad estimada de sondas, carga ICMP y filas esperadas.

`pilot-validate` no ejecuta probes ni modifica la red.

Si devuelve `FAIL`, no ejecutar `pilot-run`.

---

# 7. Ejecutar el piloto de 4 horas

Solo después de `PASS` y de confirmar que la configuración corresponde a los datos autorizados:

```powershell
python nettwin.py pilot-run `
  --config $PILOT_CONFIG `
  --authorized
```

El proceso dura aproximadamente 4 horas de captura y después continúa automáticamente con análisis e informe.

Durante la ejecución:

- mantener abierta la terminal;
- no apagar ni reiniciar el servidor;
- no suspender el equipo;
- no modificar los archivos del piloto;
- no lanzar un segundo `pilot-run` sobre el mismo directorio.

El operador no debe ejecutar comandos analíticos durante la ventana.

---

# 8. Qué ocurre automáticamente al terminar las 4 horas

El mismo `pilot-run` continúa con:

```text
sellado SHA-256
-> verify-integrity estricto
-> Quality Gate
-> Events
-> Correlations
-> Evidence
-> Fingerprints
-> report.json
-> report.html
-> verify-integrity posterior
-> pilot_summary.json
```

**No hay que entrar a `interface_samples.csv`, `host_samples.csv` ni `probe_samples.csv` para obtener el resultado.**

---

# 9. Resultado esperado

Al final debe aparecer algo equivalente a:

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

# 10. Dónde quedan los datos

Todo queda dentro de la carpeta del `RUN_ID`:

```text
pilotos/
└── HACHENET-20260810-LINK01-001/
    ├── hachenet_pilot.json
    ├── pilot_summary.json
    ├── run/
    │   ├── interface_samples.csv
    │   ├── host_samples.csv
    │   ├── probe_samples.csv
    │   ├── experiment_config.json
    │   ├── preflight.json
    │   ├── orchestration.json
    │   ├── run_metadata.json
    │   ├── checksums.sha256
    │   └── logs/linkprobe.log
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

Los CSV de `run/` son evidencia original y deben conservarse sin modificar.

---

# 11. Qué mira el técnico

Solo necesita revisar:

## Estado general

```text
pilot_summary.json
```

Debe indicar `status = PASS` y todas las etapas `PASS`.

## Informe técnico

```text
report/report.html
```

Abrir con:

```powershell
Start-Process (
  Resolve-Path "$PILOT_DIR\report\report.html"
)
```

---

# 12. Qué entrega HacheNet

Entregar la carpeta completa:

```text
pilotos\HACHENET-...\
```

No editar archivos de `run/` ni `checksums.sha256`.

La carpeta contiene configuración, evidencia, integridad, análisis e informe final.

---

# 13. Verificación opcional antes de entregar

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

---

# 14. Seguridad

NetTwin no:

- captura payload de clientes;
- inspecciona conversaciones o navegación;
- escanea rangos;
- descubre hosts automáticamente;
- modifica firewall;
- modifica routing;
- modifica interfaces;
- modifica servicios;
- ejecuta throughput agresivo.

Los probes productivos solo pueden utilizar targets explícitamente configurados y autorizados.

---

# Resumen para el operador

```text
1. Instalar.
2. Definir datos autorizados.
3. Crear configuración.
4. Completarla por PowerShell.
5. Ejecutar pilot-validate.
6. Solo si PASS: pilot-run --authorized.
7. Dejar correr 4 horas sin intervenir.
8. Esperar el análisis automático.
9. Confirmar PASS.
10. Abrir report/report.html.
11. Entregar la carpeta completa.
```

**El técnico no analiza CSVs manualmente. NetTwin genera el resultado automáticamente.**
