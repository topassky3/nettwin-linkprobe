# Active Probe Engine — Metodología de medición

## Alcance

El Active Probe Engine realiza únicamente sondas ICMP Echo pequeñas contra destinos explícitamente autorizados. No descubre hosts, no escanea rangos, no captura payload de usuarios y no modifica la configuración de red.

## Configuración por defecto

- intervalo entre ciclos: 5 s;
- ICMP Echo por target y ciclo: 1;
- payload ICMP: 32 bytes;
- timeout: 1000 ms;
- destinos: únicamente los indicados mediante configuración/CLI.

Los valores son configurables. Para el piloto de HacheNet los targets definitivos deben ser aprobados por el administrador.

## RTT

Para cada target se ejecuta `ping` mediante una lista de argumentos y `shell=False`.

El motor extrae los RTT de las respuestas recibidas y registra:

- `rtt_ms`: mediana de los RTT válidos del lote;
- `rtt_min_ms`: RTT mínimo observado en el lote;
- `rtt_max_ms`: RTT máximo observado en el lote.

Con el valor por defecto de un Echo por ciclo, los tres valores coinciden cuando existe respuesta.

Una salida `<1ms` se representa como `0.5 ms` únicamente para mantener una representación numérica reproducible. Esta aproximación debe declararse si aparece en un informe.

## Reachability

`reachability = true` cuando se recibe al menos una respuesta ICMP válida dentro del lote.

`reachability = false` cuando no se recibe ninguna respuesta.

Un target no alcanzable es un resultado de medición normal (`sample_status=ok`), no un fallo interno del sensor.

## Pérdida

La pérdida por lote se calcula como:

```text
packet_loss_pct = (packets_sent - packets_received) / packets_sent * 100
```

Con `count_per_target=1`, cada muestra individual tendrá 0 % o 100 %. La pérdida representativa de una ventana se calculará posteriormente agregando todas las sondas de esa ventana, no promediando conclusiones arbitrarias.

## Variación temporal de retardo

La métrica `delay_variation_ms` se define para este piloto como:

```text
abs(rtt_median_actual - rtt_median_anterior)
```

por target.

Es una medida operacional de cambio temporal de RTT entre muestras consecutivas. No se presenta como jitter unidireccional ni pretende sustituir una medición especializada de one-way delay.

La primera muestra válida de cada target tiene `delay_variation_ms = N/D` porque no existe una muestra anterior con la cual comparar.

## Aislamiento de fallos

Cada target se prueba independientemente.

- Un target caído no detiene los demás targets.
- Una excepción de ejecución se registra como `sample_status=error` para ese target.
- El siguiente target y el siguiente ciclo continúan normalmente.

## Presupuesto de tráfico

El motor fuerza un payload pequeño y explícito.

El payload saliente estimado por ciclo es:

```text
targets * count_per_target * payload_bytes
```

Ejemplo con tres targets, un Echo por target y payload de 32 bytes:

```text
3 * 1 * 32 = 96 bytes de payload ICMP saliente por ciclo
```

Con un ciclo cada 5 segundos durante cuatro horas:

```text
4 h * 3600 / 5 = 2880 ciclos
2880 * 96 = 276480 bytes
```

aproximadamente 270 KiB de payload ICMP saliente durante toda la ventana, sin contar cabeceras de red ni respuestas. El objetivo no es medir throughput ni saturar el enlace.

## Campos producidos

```text
timestamp
target
address
probe_type
packets_sent
packets_received
packet_loss_pct
reachability
rtt_ms
rtt_min_ms
rtt_max_ms
delay_variation_ms
payload_bytes
estimated_outbound_payload_bytes
sample_status
error
```

## Limitaciones

- ICMP puede recibir tratamiento distinto al tráfico de aplicaciones.
- Un destino externo incorpora segmentos de red fuera del control del ISP.
- La ausencia de respuesta ICMP no demuestra por sí sola una caída total del servicio.
- Por eso el piloto debe usar varios puntos de referencia autorizados: interno, controlado externo y referencia pública cuando corresponda.
- Las sondas activas complementan, pero no sustituyen, los contadores de interfaz y la telemetría del host.
