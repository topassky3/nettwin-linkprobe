# Quality Engine — metodología de la ventana observada

El Quality Engine transforma los CSV crudos de `collect-interface` y `collect-probes` en métricas resumidas y series procesadas para el piloto NetTwin LinkProbe.

## Alcance

Describe exclusivamente la ventana observada. No estima disponibilidad histórica, crecimiento mensual, fecha de saturación ni capacidad futura.

## RTT

Por target, usando únicamente valores RTT disponibles:

- mínimo;
- mediana (P50);
- P95;
- P99;
- máximo.

Los percentiles se calculan con `pandas.Series.quantile`, método lineal por defecto. Las muestras sin respuesta no reciben RTT inventado y se excluyen de los percentiles.

## Pérdida

Por target:

```text
total_probes      = suma(packets_sent)
successful_probes = suma(packets_received)
lost_probes       = max(0, total_probes - successful_probes)
loss_percent      = lost_probes / total_probes * 100
```

La pérdida se calcula desde enviados/recibidos, no promediando porcentajes de filas.

## Disponibilidad observada

```text
availability_observed_pct = ciclos con reachability=True / ciclos observados * 100
```

Esta métrica solo representa la ventana capturada. Nunca debe presentarse como disponibilidad histórica o SLA del ISP.

## Variación temporal del retardo

El Active Probe Engine define:

```text
delay_variation_ms = abs(RTT_mediano_actual - RTT_mediano_anterior)
```

El Quality Engine resume esa serie con mediana, P95 y máximo.

No es jitter unidireccional y el informe debe conservar esta limitación explícita.

## Rates de interfaz

Para cada lectura válida de contadores:

```text
rx_rate_mbps = rx_bytes_delta * 8 / elapsed_seconds / 1e6
tx_rate_mbps = tx_bytes_delta * 8 / elapsed_seconds / 1e6
```

`elapsed_seconds` se mide desde la última lectura válida de contadores de la misma interfaz. Esto evita sobreestimar el rate cuando hubo una muestra de error intermedia y el siguiente delta abarca un intervalo mayor.

Deltas ausentes, resets y tiempos no positivos no generan rates artificiales.

## Utilización

La utilización solo se calcula cuando la capacidad del enlace se suministra explícitamente:

```text
rx_utilization_pct = rx_rate_mbps / capacity_mbps * 100
tx_utilization_pct = tx_rate_mbps / capacity_mbps * 100
utilization_pct    = max(rx_utilization_pct, tx_utilization_pct)
```

La velocidad reportada por la NIC (`reported_link_speed_mbps`) no se interpreta como capacidad comercial del enlace del ISP.

Si no existe una capacidad autorizada/conocida, la utilización queda como N/D.

## Errors y drops

Se suman los deltas válidos observados en la ventana:

- `rx_errors_delta_total`;
- `tx_errors_delta_total`;
- `rx_drops_delta_total`;
- `tx_drops_delta_total`.

No se fabrican valores para muestras ausentes.

## Archivos generados

```text
quality_summary.json
quality_interface_summary.csv
quality_probe_summary.csv
quality_interface_processed.csv
quality_probe_processed.csv
```

Los archivos `processed` conservan las series derivadas que permitirán a las fases posteriores detectar eventos y correlacionar métricas.

## Uso

Sin capacidad conocida:

```bash
python nettwin.py analyze-quality \
  --interface-csv runs/interface_samples.csv \
  --probe-csv runs/probe_samples.csv \
  --output resultados_quality
```

Con capacidad explícita autorizada:

```bash
python nettwin.py analyze-quality \
  --interface-csv runs/interface_samples.csv \
  --probe-csv runs/probe_samples.csv \
  --capacity-mbps 150 \
  --output resultados_quality
```

## Principio de interpretación

El Quality Engine calcula métricas. No diagnostica por sí solo la causa de un cambio. La causalidad, hipótesis y nivel de confianza pertenecen a Event Engine, Correlation Engine y Evidence Engine.
