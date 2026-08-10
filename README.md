# NetTwin LinkProbe v0.3

NetTwin LinkProbe mide la calidad de un enlace durante una **ventana observada**, conserva la evidencia original y genera automáticamente un informe técnico reproducible.

Versión actual: **NetTwin ISP 0.3.0**.

> ## Qué debe hacer la persona que ejecuta el piloto
>
> No tiene que analizar CSV, calcular métricas ni ejecutar motores manualmente.
>
> El flujo es:
>
> ```text
> instalar
>   ↓
> comprobar que NetTwin funciona localmente
>   ↓
> identificar y confirmar los datos del enlace
>   ↓
> crear la configuración
>   ↓
> validar sin iniciar la medición
>   ↓
> ejecutar un único pilot-run
>   ↓
> dejarlo correr 4 horas
>   ↓
> NetTwin analiza automáticamente
>   ↓
> abrir report.html
> ```
>
> Los CSV quedan guardados y sellados como evidencia. **No son una tarea manual para la persona que ejecuta el piloto.**

---

# 1. Qué hace NetTwin automáticamente

Cuando comienza `pilot-run`, NetTwin ejecuta por sí solo:

```text
validación
   ↓
autorización
   ↓
preflight
   ↓
4 HORAS DE CAPTURA COORDINADA
   ├── Interface Collector
   ├── Host Collector
   └── Active Probe Engine
   ↓
sellado SHA-256
   ↓
verificación de integridad
   ↓
Quality Gate
   ↓
Event Engine
   ↓
Correlation Engine
   ↓
Evidence Engine
   ↓
Event Fingerprints
   ↓
Link Health Audit
   ↓
segunda verificación de integridad
   ↓
pilot_summary.json
report/report.html
```

Después de iniciar `pilot-run` **no hay que abrir CSV ni ejecutar comandos de análisis**.

---

# 2. Qué registra durante las 4 horas

NetTwin registra métricas, no contenido de usuarios.

No captura payload, conversaciones ni navegación.

## Tráfico de la interfaz observada

`run/interface_samples.csv` conserva, entre otras métricas:

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

## Salud del servidor

`run/host_samples.csv` conserva métricas de CPU, RAM y estado del host.

## Sondas autorizadas

`run/probe_samples.csv` conserva reachability, pérdida y RTT cuando puede medirse de forma fiable.

Estos archivos son evidencia. El resultado que normalmente se consulta es:

```text
report/report.html
```

---

# 3. Instalación

## Windows / PowerShell

```powershell
git clone https://github.com/topassky3/nettwin-linkprobe.git
cd nettwin-linkprobe
python -m pip install -r requirements.txt
python nettwin.py --version
```

Debe aparecer:

```text
NetTwin ISP 0.3.0
```

Si el repositorio ya estaba clonado:

```powershell
git switch main
git pull origin main
python -m pip install -r requirements.txt
python nettwin.py --version
```

---

# 4. Primero: prueba local de 12 segundos

Antes de preparar un enlace real, comprueba que la instalación funciona.

Esta prueba usa solamente `127.0.0.1` y no envía sondas a una red externa.

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

Al finalizar debe verse:

```text
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
```

Abrir el informe local:

```powershell
Start-Process (
  Resolve-Path prueba_nettwin_local\report\report.html
)
```

Si esta prueba termina en `PASS`, continúa con el piloto real.

---

# 5. Antes del piloto real: reunir la información correcta

**No copies todavía un bloque de variables.**

Primero obtén o confirma cada dato siguiendo esta sección.

Para un primer piloto basta con **un target autorizado**.

---

## Dato A — cuál interfaz representa el enlace que quieres observar

NetTwin solo puede medir los contadores de la interfaz seleccionada. Por eso hay que escoger la interfaz por la que realmente pasa el tráfico del enlace objetivo.

### Paso A1 — listar las interfaces que ve NetTwin

```powershell
python nettwin.py interfaces
```

También puedes ver información de Windows con:

```powershell
Get-NetIPConfiguration |
  Select-Object InterfaceAlias, InterfaceDescription, IPv4Address, IPv4DefaultGateway |
  Format-Table -AutoSize
```

### Paso A2 — ver la ruta por defecto

Este comando es de solo lectura:

```powershell
Get-NetRoute `
  -AddressFamily IPv4 `
  -DestinationPrefix "0.0.0.0/0" |
  Sort-Object RouteMetric |
  Select-Object InterfaceAlias, NextHop, RouteMetric |
  Format-Table -AutoSize
```

### Cómo elegir

Usa el **nombre exacto** de la interfaz que corresponde al enlace que se quiere medir, por ejemplo:

```text
Ethernet
```

o:

```text
Ethernet 2
```

No elijas una interfaz solo porque aparece `UP`.

Si hay varias interfaces y no está claro cuál lleva el enlace objetivo, **detente y confirma ese dato con la persona que conoce la conexión del servidor**.

No uses Tailscale, VPN, WSL, Hyper-V u otra interfaz virtual salvo que esa sea específicamente la red que se pretende medir.

Cuando ya esté claro, anota:

```text
INTERFACE = nombre exacto de la interfaz
```

---

## Dato B — qué IP usar como target

Un target es simplemente un punto de referencia al que NetTwin enviará pequeñas sondas ICMP para observar reachability, pérdida y RTT cuando sea medible.

**NetTwin no descubre targets automáticamente.**

La IP debe ser conocida y estar autorizada antes de usarla.

### Paso B1 — ver el gateway como posible referencia

Puedes consultar el gateway sin enviar ninguna sonda:

```powershell
Get-NetRoute `
  -AddressFamily IPv4 `
  -DestinationPrefix "0.0.0.0/0" |
  Sort-Object RouteMetric |
  Select-Object InterfaceAlias, NextHop, RouteMetric |
  Format-Table -AutoSize
```

`NextHop` muestra el siguiente salto de cada ruta.

**Que una IP aparezca aquí no significa automáticamente que esté autorizada como target.**

### Paso B2 — confirmar un target autorizado

La persona que conoce o administra el enlace debe indicar una IP estable que pueda recibir las sondas ICMP del piloto.

Puede ser, por ejemplo:

- un router/upstream conocido del enlace;
- un servidor de referencia controlado;
- otro endpoint estable cuya utilización como referencia haya sido aprobada.

Para el primer piloto utiliza un solo target si no existe una razón clara para añadir más.

No uses una IP escogida al azar de Internet.

Cuando esté confirmado, anota:

```text
TARGET_IP = IP real autorizada
```

---

## Dato C — qué significa ese target

El campo `role` no es un dato que haya que descubrir con un comando. Es una **etiqueta descriptiva** para que el informe recuerde qué representa esa IP.

Escoge una etiqueta según el caso real:

```text
upstream_reference
```

si es un router o referencia aguas arriba del enlace.

```text
controlled_reference
```

si es un servidor o endpoint controlado usado como referencia.

```text
external_reference
```

si es una referencia externa explícitamente aprobada.

Si ninguna describe bien el target, utiliza una etiqueta corta que explique su función.

Ejemplo:

```text
core_router_reference
```

Anota:

```text
TARGET_ROLE = qué representa la IP
```

El nombre del target puede ser sencillo:

```text
target-01
```

No necesitas inventar un nombre complejo.

---

## Dato D — quién autorizó las sondas y dónde quedó registrada la aprobación

Antes de ejecutar el piloto real debe existir una aprobación verificable para usar ese target.

Necesitas dos datos:

```text
APPROVED_BY
AUTH_REF
```

### APPROVED_BY

Es el nombre o identificación de la persona responsable que aprobó el uso de los targets para el piloto.

Ejemplo:

```text
Nombre Apellido - Operaciones
```

### AUTH_REF

Es la referencia que permite volver a encontrar la aprobación.

Puede ser, por ejemplo:

```text
Ticket CHG-1234
```

```text
Correo "Piloto NetTwin LINK01" 2026-08-10
```

```text
Reunión de aprobación 2026-08-10
```

No escribas `autorizado` sin una referencia identificable.

---

## Dato E — capacidad real del enlace

NetTwin puede calcular utilización porcentual únicamente cuando conoce la capacidad real del enlace.

Ese dato debe provenir de una fuente real, por ejemplo:

- capacidad contratada;
- configuración del enlace;
- inventario/NMS;
- información confirmada por quien opera el enlace.

**No uses la velocidad que Windows muestra para la tarjeta de red como capacidad del enlace.**

Si conoces de forma fiable que el enlace es de 300 Mbps:

```powershell
$CAPACITY_MBPS = 300
```

Si no lo sabes:

```powershell
$CAPACITY_MBPS = $null
```

Dejarlo en `$null` es correcto. El informe mostrará utilización como `N/D` en lugar de inventarla.

---

## Dato F — identificador del enlace

Necesitamos una etiqueta corta para distinguir este enlace de otros pilotos.

Para un primer enlace puedes utilizar:

```text
LINK01
```

Si existe un nombre interno claro, puedes usarlo en lugar de `LINK01`, siempre evitando espacios complicados.

Anota:

```text
LINK_ID = LINK01
```

El `RUN_ID` se generará automáticamente con la fecha; no hace falta inventarlo manualmente.

---

# 6. Checklist antes de continuar

No sigas hasta poder completar esta tabla mental:

```text
INTERFACE      = interfaz exacta del enlace
TARGET_IP      = IP estable y autorizada
TARGET_ROLE    = qué representa esa IP
APPROVED_BY    = quién aprobó las sondas
AUTH_REF       = dónde quedó registrada la aprobación
CAPACITY_MBPS  = número real o N/D
LINK_ID        = identificador corto del enlace
```

Si falta `CAPACITY_MBPS`, puedes continuar con `$null`.

Si falta cualquiera de los otros datos, **no inicies el piloto real todavía**.

---

# 7. Ahora sí: colocar los datos en PowerShell

Una vez recopilada la información anterior, define las variables.

Ejemplo de estructura:

```powershell
$LINK_ID       = "LINK01"
$INTERFACE     = "REEMPLAZAR_CON_INTERFAZ_CONFIRMADA"
$TARGET_IP     = "REEMPLAZAR_CON_IP_AUTORIZADA"
$TARGET_ROLE   = "upstream_reference"
$APPROVED_BY   = "REEMPLAZAR_CON_RESPONSABLE_REAL"
$AUTH_REF      = "REEMPLAZAR_CON_REFERENCIA_REAL"

# Ejemplo si la capacidad real fuera 300 Mbps:
# $CAPACITY_MBPS = 300

# Si no se conoce de forma fiable:
$CAPACITY_MBPS = $null
```

Ahora NetTwin puede construir automáticamente los demás identificadores:

```powershell
$DATE = Get-Date -Format "yyyyMMdd"
$RUN_ID = "HACHENET-$DATE-$LINK_ID-001"
$TARGET_NAME = "target-01"

$PILOT_DIR = "pilotos\$RUN_ID"
$PILOT_CONFIG = "$PILOT_DIR\hachenet_pilot.json"
```

Comprueba lo que vas a utilizar:

```powershell
[PSCustomObject]@{
  RunId        = $RUN_ID
  Link         = $LINK_ID
  Interface    = $INTERFACE
  TargetName   = $TARGET_NAME
  TargetIP     = $TARGET_IP
  TargetRole   = $TARGET_ROLE
  ApprovedBy   = $APPROVED_BY
  AuthRef      = $AUTH_REF
  CapacityMbps = $CAPACITY_MBPS
} | Format-List
```

**Lee esa salida antes de continuar.**

Si algo está mal, corrige la variable correspondiente ahora.

---

# 8. Crear la carpeta y la plantilla del piloto

```powershell
New-Item `
  -ItemType Directory `
  -Path $PILOT_DIR `
  -Force |
  Out-Null

python nettwin.py pilot-config `
  --output $PILOT_CONFIG `
  --interface $INTERFACE `
  --run-id $RUN_ID
```

Debe aparecer:

```text
Modo: hachenet_pilot
Estado: PLANTILLA NO EJECUTABLE
```

Eso es correcto. Todavía falta introducir en el JSON el target y la aprobación.

---

# 9. Completar automáticamente el JSON

No necesitas editar `hachenet_pilot.json` a mano.

Copia y ejecuta este bloque:

```powershell
$c = Get-Content `
  $PILOT_CONFIG `
  -Encoding UTF8 |
  ConvertFrom-Json

$c.run.run_id = $RUN_ID
$c.run.duration_seconds = 14400

$c.interface.name = $INTERFACE
$c.interface.capacity_mbps = $CAPACITY_MBPS

$c.targets[0].name = $TARGET_NAME
$c.targets[0].address = $TARGET_IP
$c.targets[0].role = $TARGET_ROLE
$c.targets[0].authorized = $true

$c.pilot.link_name = $LINK_ID
$c.pilot.authorization.confirmed = $true
$c.pilot.authorization.reference = $AUTH_REF
$c.pilot.authorization.approved_by = $APPROVED_BY

$c |
  ConvertTo-Json -Depth 20 |
  Set-Content $PILOT_CONFIG -Encoding UTF8
```

Comprueba que el archivo existe:

```powershell
Test-Path $PILOT_CONFIG
```

Debe devolver:

```text
True
```

---

# 10. Validar SIN comenzar las 4 horas

Ahora ejecuta:

```powershell
python nettwin.py pilot-validate `
  --config $PILOT_CONFIG

$LASTEXITCODE
```

`pilot-validate` no inicia la captura y no modifica la red.

## Resultado requerido

Debe aparecer:

```text
Modo: hachenet_pilot
Estado: PASS
```

Y después:

```text
0
```

También mostrará:

- duración: `4.000 h`;
- cantidad de targets;
- ciclos de sondas;
- ICMP echo requests estimados;
- payload estimado;
- cantidad esperada de filas;
- advertencias.

## Si aparece una advertencia de capacidad

Por ejemplo:

```text
interface.capacity_mbps es N/D
```

no es un error si deliberadamente dejaste:

```powershell
$CAPACITY_MBPS = $null
```

## Si aparece FAIL

**No ejecutes `pilot-run`.**

Revisa primero el mensaje:

- interfaz incorrecta → vuelve al Dato A;
- IP inválida → vuelve al Dato B;
- target no autorizado → revisa el Dato D y el JSON;
- falta responsable/referencia → vuelve al Dato D;
- placeholder todavía presente → corrige la variable correspondiente;
- duración distinta de 14400 → no modifiques la duración productiva.

Vuelve a ejecutar `pilot-validate` hasta obtener `PASS`.

---

# 11. Ejecutar el piloto real de 4 horas

Solo después de obtener `PASS`:

```powershell
python nettwin.py pilot-run `
  --config $PILOT_CONFIG `
  --authorized
```

El flag `--authorized` es la confirmación final de que los datos mostrados y la aprobación fueron revisados.

---

# 12. Qué hacer durante las 4 horas

Después de iniciar `pilot-run`, **no hay que ejecutar nada más**.

Deja la misma terminal abierta.

Durante la ventana:

- no cierres la terminal;
- no apagues ni reinicies el servidor;
- evita suspender el equipo;
- no modifiques los archivos dentro de `$PILOT_DIR`;
- no ejecutes otro `pilot-run` sobre ese mismo directorio.

NetTwin irá escribiendo las muestras durante la captura.

Cuando termine la ventana, el mismo proceso continuará automáticamente con:

```text
sellado SHA-256
   ↓
verificación
   ↓
Quality Gate
   ↓
eventos
   ↓
correlaciones
   ↓
evidencia
   ↓
fingerprints
   ↓
informe
   ↓
segunda verificación
```

**No abras ni analices manualmente los CSV para completar el piloto.**

---

# 13. Qué debe aparecer al finalizar

El mismo comando debe terminar mostrando algo equivalente a:

```text
Fase 15 finalizada.
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
```

Después comprueba:

```powershell
$LASTEXITCODE
```

Debe devolver:

```text
0
```

---

# 14. Abrir el resultado

El informe final queda en:

```text
report\report.html
```

Ábrelo con:

```powershell
Start-Process (
  Resolve-Path "$PILOT_DIR\report\report.html"
)
```

Ese es el resultado principal que se debe leer.

---

# 15. Comprobar el estado del piloto

También puedes revisar el resumen estructurado:

```powershell
$s = Get-Content `
  "$PILOT_DIR\pilot_summary.json" `
  -Encoding UTF8 |
  ConvertFrom-Json

$s |
  Select-Object `
    status,
    mode,
    run_id,
    software_version,
    authorization_gate_passed |
  Format-List

$s.stages | Format-List
```

Para un piloto correcto se espera:

```text
status                    : PASS
authorization_gate_passed : True

validation              : PASS
preflight_capture       : PASS
integrity_before_report : PASS
analysis_report         : PASS
integrity_after_report  : PASS
```

---

# 16. Dónde quedan los datos

Todo el piloto queda aislado dentro de su propio directorio:

```text
pilotos/
└── HACHENET-AAAAMMDD-LINK01-001/
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

## Lo que normalmente se consulta

```text
pilot_summary.json
report/report.html
```

## Lo que se conserva como evidencia

```text
run/
```

No edites manualmente los archivos de `run/` después de la captura.

---

# 17. Verificar integridad antes de entregar

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

# 18. Empaquetar todo el piloto

Si necesitas entregar el resultado completo:

```powershell
$ZIP = "$PILOT_DIR.zip"

Compress-Archive `
  -Path $PILOT_DIR `
  -DestinationPath $ZIP

Write-Host "Paquete generado: $ZIP"
```

Entrega el ZIP completo. No es necesario separar manualmente los CSV del informe.

---

# 19. Qué significa un resultado sin eventos

Un resultado como:

```text
events = 0
findings = 0
```

no significa que el programa falló.

Significa que durante esa ventana y con los umbrales configurados no se detectaron eventos que justificaran un hallazgo.

NetTwin no inventa problemas para llenar el informe.

---

# 20. Limitaciones importantes

El piloto describe **esas cuatro horas observadas**.

No convierte automáticamente la medición en:

- SLA histórico;
- disponibilidad mensual;
- comportamiento semanal;
- capacidad física máxima;
- causa definitiva de una degradación.

La secuencia del análisis es:

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

---

# 21. Seguridad por diseño

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

En modo productivo se limita además a:

- máximo 3 targets;
- targets explícitamente marcados `authorized=true`;
- intervalo de probes >= 2 s;
- payload ICMP <= 128 bytes;
- máximo 3 echoes por target/ciclo;
- duración exactamente 14400 s;
- rechazo de loopback, multicast, link-local y redes reservadas para documentación.

---

# 22. Resumen ultracorto

Si ya conoces el proceso, la ruta es:

```text
1. python nettwin.py interfaces
2. confirmar interfaz real
3. confirmar una IP target autorizada
4. registrar quién autorizó y la referencia
5. capacidad real o N/D
6. definir variables
7. pilot-config
8. completar JSON por PowerShell
9. pilot-validate
10. comprobar PASS
11. pilot-run --authorized
12. dejar correr 4 horas
13. abrir report/report.html
14. verificar integridad
15. empaquetar la carpeta del piloto
```

---

# 23. Documentación adicional

- `docs/GUIA_CLIENTE_HACHENET.md`
- `docs/PILOT_HACHENET.md`
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
Dry-run integral PASS
Aceptación local Fase 15 PASS
Integridad estricta PASS
Report Engine PASS
```
