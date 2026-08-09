# NetTwin LinkProbe v0.3 — Preflight operacional

## Objetivo

Fase 11 implementa el diagnóstico previo obligatorio del piloto. Su propósito es decidir si el host está operacionalmente preparado para iniciar la medición y conservar una fotografía técnica del entorno antes del experimento.

El preflight no mide la calidad del enlace y no intenta diagnosticar tráfico de clientes.

## Principio de seguridad

El modo `preflight` es observacional.

Durante esta fase NetTwin NO:

- envía ICMP a los targets;
- resuelve DNS de los targets;
- modifica rutas;
- modifica firewall;
- modifica interfaces;
- modifica servicios;
- modifica configuración de red;
- descubre equipos;
- captura payload.

La única consulta de red del sistema que puede ejecutar es una inspección local y de solo lectura de la tabla de rutas para intentar identificar el gateway predeterminado.

Por plataforma:

```text
Windows: route PRINT -4 0.0.0.0
Linux:   ip -4 route show default
macOS:   route -n get default
```

Estas órdenes no modifican rutas.

## Configuración

Fase 11 usa JSON deliberadamente para no añadir una dependencia YAML al proyecto.

Estructura:

```json
{
  "run": {
    "run_id": "HACHENET-20260809-LINK01-001",
    "duration_seconds": 14400
  },
  "interface": {
    "name": "eth0",
    "interval_seconds": 5,
    "capacity_mbps": 100
  },
  "host": {
    "interval_seconds": 5
  },
  "probes": {
    "interval_seconds": 5,
    "timeout_ms": 1000,
    "payload_bytes": 32,
    "count_per_target": 1
  },
  "targets": [
    {
      "name": "external_reference",
      "address": "203.0.113.1",
      "role": "public_reference"
    }
  ],
  "output": {
    "directory": "./runs/HACHENET-20260809-LINK01-001",
    "minimum_free_disk_mb": 50
  }
}
```

`capacity_mbps` es opcional. Si no se conoce debe ser `null`; la velocidad reportada por la NIC nunca sustituye automáticamente la capacidad del servicio ISP.

Los destinos definitivos del piloto deben ser suministrados o aprobados por HacheNet. Las direcciones TEST-NET incluidas en ejemplos no son destinos del piloto.

## Ejecución

```powershell
python nettwin.py preflight `
  --config preflight_config.json `
  --sensor-version "0.3-dev"
```

Por defecto el resultado se escribe en:

```text
<output.directory>/preflight.json
```

Se puede elegir otra ruta con:

```text
--output ruta\preflight.json
```

## Información registrada

`preflight.json` conserva:

- run_id;
- versión del sensor;
- versión de Python;
- versión de psutil;
- SHA-256 de la configuración;
- sistema operativo;
- arquitectura;
- hora UTC;
- hora local;
- zona horaria;
- hostname anonimizado;
- interfaz seleccionada;
- estado UP/DOWN;
- disponibilidad de contadores;
- MTU;
- velocidad reportada por NIC;
- direcciones IPv4/IPv6 de la interfaz cuando no se redactan;
- gateway observado cuando puede determinarse;
- disponibilidad del ejecutable `ping`;
- estado de privilegios;
- posibilidad de escritura;
- espacio total/libre;
- targets y roles;
- configuración de sondas;
- número aproximado de muestras esperadas;
- payload ICMP saliente estimado;
- checks PASS/WARN/FAIL;
- garantías explícitas de no modificación.

## Hostname

Por privacidad, el hostname en texto claro NO se almacena por defecto.

Se almacena:

```text
SHA256(run_id + NUL + hostname)
```

El hash queda limitado al `run_id`, evitando usar el preflight como identificador persistente del host entre ejecuciones.

Para una sesión autorizada donde sea necesario conservar el hostname:

```text
--include-hostname
```

## IP local

Las IP locales se almacenan por defecto porque pueden ser necesarias para interpretar la interfaz y el gateway del host autorizado.

Si el administrador desea redactarlas:

```text
--redact-local-addresses
```

En ese modo se conserva un SHA-256 de cada dirección, no su valor en claro.

La redacción afecta únicamente al artefacto; la aplicación puede utilizar internamente la información local necesaria para inspección del host.

## Targets

Por cada target se registra:

```text
name
address
role
address_type
syntax_valid
dns_resolution_performed = false
active_probe_performed = false
```

Se aceptan IPv4, IPv6 y hostname sin esquema, path ni puerto.

Ejemplos válidos:

```text
192.0.2.1
2001:db8::1
probe.example.net
```

Ejemplos rechazados:

```text
https://example.net
example.net:443
example.net/path
```

Los nombres de target deben ser únicos.

## Checks obligatorios

Un FAIL en cualquiera de estos checks impide marcar el host como listo:

```text
interface.exists
interface.up
interface.counters
tool.ping
output.writable
disk.free_space
targets.syntax
clock.timezone
```

`ready_for_run` será `false` si existe al menos un FAIL.

## Checks informativos

Pueden producir WARN sin impedir por sí solos la ejecución:

```text
interface.local_address
interface.reported_speed
gateway.observed
targets.count
```

Un WARN debe revisarse antes del piloto; no debe ignorarse automáticamente.

## Gateway

La observación del gateway es best-effort.

Una imposibilidad de leerlo no bloquea automáticamente la ejecución porque existen servidores, namespaces, bridges, contenedores y topologías donde el concepto de gateway visto desde el proceso no representa necesariamente el enlace auditado.

El artefacto registra:

```text
status
gateway
interface/interface_ip
source
tool
tool_path
command
command_was_read_only
error
```

## Permisos

El preflight registra si el proceso parece elevado, pero LinkProbe v0.3 no exige privilegios administrativos como condición general.

Active Probe Engine usa el ejecutable `ping` del sistema y no crea raw sockets directamente.

Los permisos operativos realmente obligatorios en Fase 11 son:

- leer la interfaz mediante APIs del sistema/psutil;
- disponer de contadores;
- poder escribir los artefactos del run;
- disponer de `ping` para la fase de sondas posterior.

## Disco

`minimum_free_disk_mb` es un umbral operacional configurable.

El preflight registra:

```text
total_bytes
used_bytes
free_bytes
minimum_required_bytes
enough_free_space
```

No se interpreta este umbral como una predicción exacta del tamaño final del dataset.

## Estimación de muestras

Para una duración `D` y un intervalo `T`:

```text
planned_cycles = ceil(D / T)
```

Se calcula por separado para:

- Interface Collector;
- Host Collector;
- Active Probe Engine.

Con 4 horas, intervalos de 5 s y tres targets:

```text
interface_rows          2880
host_rows               2880
probe_cycles/target     2880
probe_rows_total        8640
total_rows             14400
```

Es una expectativa de planificación. El control de calidad posterior debe usar las muestras realmente recibidas.

## Estimación de tráfico activo

El preflight NO genera tráfico activo.

Solo calcula la estimación que produciría posteriormente Active Probe Engine:

```text
probe_cycles
× targets
× count_per_target
× payload_bytes
```

La cifra representa únicamente payload ICMP saliente.

No incluye:

- cabecera Ethernet;
- cabecera IP;
- cabecera ICMP;
- respuestas;
- overhead adicional del sistema.

Por eso se etiqueta explícitamente como estimación de payload, no como volumen exacto sobre el enlace.

## Estados

### PASS

Todos los checks obligatorios e informativos están disponibles.

### WARN

No existen FAIL, pero uno o más checks informativos requieren revisión.

`ready_for_run` puede seguir siendo `true`.

### FAIL

Existe al menos un check obligatorio fallido.

`ready_for_run` será `false` y la medición no debería iniciarse hasta corregirlo o documentar un cambio explícito de configuración.

## network_safety

Todo `preflight.json` contiene:

```json
{
  "active_probe_performed": false,
  "dns_resolution_performed": false,
  "routes_modified": false,
  "firewall_modified": false,
  "interfaces_modified": false,
  "services_modified": false,
  "network_configuration_modified": false
}
```

Esto no pretende ser una prueba criptográfica de ausencia de cambios externos. Es el registro de operaciones que el módulo Preflight de NetTwin está diseñado para realizar.

## Limitaciones

- Preflight no confirma reachability de targets.
- Preflight no confirma que ICMP sea permitido por el ISP.
- Preflight no demuestra calidad del enlace.
- Preflight no sustituye autorización del administrador.
- El gateway puede ser N/D en topologías especiales.
- La velocidad NIC no es capacidad del servicio.
- Los conteos de muestras son expectativas, no datos observados.
- La hora se registra tal como la reporta el host; la validación de sincronización NTP profunda queda fuera de esta fase.

## Criterio de aceptación de Fase 11

La fase queda aceptada cuando:

1. `preflight.json` se genera con configuración válida.
2. Se registran OS, hostname anonimizable, hora y zona horaria.
3. Se registra interfaz, MTU, velocidad reportada, contadores e IP local cuando no se redacta.
4. Gateway se registra o queda N/D con explicación.
5. Se registran permisos y herramientas.
6. Se registran targets sin generar tráfico.
7. Se registra disco disponible.
8. Se registra versión y SHA-256 de configuración.
9. Se calculan muestras y payload estimado.
10. Una interfaz inexistente/down produce FAIL.
11. Falta de `ping` produce FAIL.
12. Disco insuficiente produce FAIL.
13. Falta de gateway produce WARN, no inventa un valor.
14. El artefacto declara cero modificaciones de red.
15. Todas las pruebas anteriores del repositorio siguen pasando.
