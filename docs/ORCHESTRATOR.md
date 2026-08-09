# Fase 12 — Orquestador de ejecución

## Objetivo

Fase 12 convierte los collectors ya validados en una ejecución coordinada de
LinkProbe. Una vez iniciado `run-linkprobe`, el proceso debe poder completar la
ventana configurada sin intervención manual y conservar evidencia cruda,
metadata, logs y checksums.

Esta fase no genera todavía el informe técnico final y no ejecuta el pipeline
analítico completo. Su responsabilidad termina en un dataset coordinado,
sellado y verificable.

## Comando

```powershell
python nettwin.py run-linkprobe --config config.json --sensor-version "0.3-dev"
```

Preflight es obligatorio. No existe una opción para saltarlo.

## Secuencia

```text
config.json
    │
    ▼
validar directorio de run vacío
    │
    ▼
preflight.json
    │
    ├── FAIL ──> bloquear captura activa
    │
    └── PASS/WARN ready_for_run=true
                │
                ▼
       copiar configuración exacta
                │
                ▼
              T0 común
        ┌───────┼────────┐
        ▼       ▼        ▼
 Interface    Host     Active Probes
 Collector  Collector     Engine
        │       │        │
        ▼       ▼        ▼
 interface   host      probe
 samples     samples   samples
        └───────┼────────┘
                ▼
         cierre coordinado
                │
                ▼
        orchestration.json
        logs/linkprobe.log
                │
                ▼
        finalize-integrity
                │
                ▼
       run_metadata.json
       checksums.sha256
                │
                ▼
     verify-integrity --strict
                │
                ▼
       integrity_report.json
```

## Un único T0

Los tres workers se preparan primero y esperan un evento común de inicio. El
orquestador fija una sola referencia monotónica `T0` y un único deadline.

Cada collector mantiene su frecuencia independiente:

- `interface.interval_seconds`;
- `host.interval_seconds`;
- `probes.interval_seconds`.

La planificación utiliza deadlines absolutos derivados de T0, no una cadena de
`sleep(interval)` acumulativa. Esto reduce drift temporal.

Ejemplo:

```text
duration = 12 s
interface = 2 s
host = 2 s
probes = 2 s

T0  T+2  T+4  T+6  T+8  T+10
```

Se esperan seis ciclos por collector. Para probes, cada ciclo produce una fila
por target.

## Rondas de probes lentas

Las rondas de Active Probe Engine nunca se solapan. Si una ronda tarda más que
el intervalo configurado:

1. la ronda actual termina;
2. se registra `overrun_cycles` y `max_scheduler_lag_seconds`;
3. la siguiente ronda comienza tan pronto como sea posible;
4. no se crea otro thread de probes para intentar alcanzar el reloj.

Esto evita multiplicar tráfico activo bajo degradación.

## Errores parciales

Las muestras con `sample_status=error` se conservan en el CSV.

Un error puntual:

- no borra la muestra;
- aumenta `error_rows`;
- queda visible en logs;
- no detiene automáticamente los otros collectors.

Un target no alcanzable es una medición, no un crash. Active Probe Engine
continúa con los otros targets autorizados.

Si un worker sufre una excepción fatal inesperada, los otros workers pueden
continuar hasta el deadline y el run queda marcado `partial_failure`.

## Estados de ejecución

`orchestration.json` puede registrar:

- `completed`;
- `completed_with_sample_errors`;
- `partial_failure`;
- `interrupted`;
- `preflight_blocked`.

`preflight_blocked` significa que no se inició captura activa.

## Señales y cierre limpio

Cuando la plataforma lo soporta se instalan handlers para:

- `SIGINT`;
- `SIGTERM`;
- `SIGBREAK` en Windows cuando existe.

La señal activa un `stop_event` compartido. Los workers terminan su operación en
curso, cierran sus CSV y el run se sella con la evidencia obtenida hasta ese
momento.

El orquestador no mata procesos de forma abrupta ni abandona handles de archivo
intencionalmente.

## Directorio de ejecución

El directorio configurado en `output.directory` representa un único run.

Por seguridad, si ya contiene archivos el orquestador se niega a comenzar. No
hay overwrite automático de evidencia previa.

Ejemplo:

```text
runs/HACHENET-20260809-LINK01-001/
├── experiment_config.json
├── preflight.json
├── interface_samples.csv
├── host_samples.csv
├── probe_samples.csv
├── orchestration.json
├── run_metadata.json
├── checksums.sha256
├── integrity_report.json
└── logs/
    └── linkprobe.log
```

## Copia de configuración

`experiment_config.json` es una copia byte a byte del archivo externo usado para
la ejecución. Esta copia queda dentro del conjunto sellado.

El objetivo es conservar:

- intervalos;
- duración;
- interfaz;
- targets;
- payload ICMP;
- timeout;
- directorio;
- cualquier otra decisión operativa del experimento.

No se debe editar después del run.

## Logging

`logs/linkprobe.log` registra:

- inicio;
- versión;
- run_id;
- interfaz;
- intervalos;
- nombres de targets;
- collectors activos;
- errores de muestras;
- cambios de reachability;
- finalización;
- inicio del sellado.

No se registran direcciones de targets en el log operacional. Las direcciones
pertenecen a la configuración sellada y a `probe_samples.csv`, donde son
necesarias para trazabilidad.

El log se cierra **antes** de generar checksums. Después del sellado no se vuelve
a modificar el archivo.

## Integridad automática

Una captura iniciada siempre intenta ejecutar:

```text
finalize_integrity
      ↓
verify_integrity(strict_untracked=True)
```

El resultado normal debe producir:

```text
run_metadata.json
checksums.sha256
integrity_report.json
```

`integrity_report.json` está excluido del conjunto auto-sellado porque es el
resultado de verificarlo.

## Seguridad

El orquestador no agrega nuevas capacidades de acceso a red.

No realiza:

- captura de payload;
- inspección de comunicaciones;
- descubrimiento de equipos;
- escaneo indiscriminado;
- cambio de rutas;
- cambio de firewall;
- cambio de interfaces;
- cambio de servicios;
- pruebas de throughput.

La única actividad de red es la ya implementada por Active Probe Engine y queda
limitada a los targets de la configuración previamente autorizada.

## Prueba local segura

Generar configuración:

```powershell
python scripts\generate_orchestrator_debug_config.py --output debug_fase12
```

La configuración de debug usa:

```text
target = 127.0.0.1
payload = 32 bytes
count = 1
```

Por tanto los ICMP permanecen en el propio host.

Ejecutar:

```powershell
python nettwin.py run-linkprobe `
  --config debug_fase12\orchestrator_config.json `
  --sensor-version "0.3-dev"
```

No reutilizar el mismo `run/` sin eliminar deliberadamente el dataset de debug
o generar otro directorio. Esta protección es intencional.

## Criterios de aceptación de Fase 12

- Preflight se ejecuta antes de probes activos.
- Preflight no listo bloquea toda captura activa.
- Los tres collectors comparten una ventana coordinada.
- Cada collector conserva su frecuencia configurada.
- Las muestras se escriben incrementalmente y se hace flush.
- Un target caído no detiene Interface/Host ni otros targets.
- Un error puntual queda registrado y no rompe el run.
- Un fallo fatal parcial queda explícito.
- SIGINT/SIGTERM producen cierre coordinado.
- El directorio de un run anterior nunca se sobrescribe silenciosamente.
- Se conserva una copia exacta de configuración.
- Se genera `logs/linkprobe.log`.
- Se genera `orchestration.json`.
- Se generan `run_metadata.json` y `checksums.sha256`.
- La verificación final estricta debe ser válida en un run normal.
- No se modifica configuración de red.

## Fuera de alcance de Fase 12

Todavía no pertenecen a esta fase:

- Quality Engine automático posterior al run;
- Event/Correlation/Evidence/Fingerprint automáticos;
- validación avanzada de calidad temporal del dataset;
- generación del informe técnico;
- PDF/HTML final.

Esas piezas se integrarán en fases posteriores y en el dry-run completo.
