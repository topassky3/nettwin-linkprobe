# Event Engine — metodología de detección temporal

El Event Engine implementa la Fase 6 de NetTwin LinkProbe v0.3. Su objetivo es transformar las series procesadas del Quality Engine en ventanas temporalmente relevantes sin afirmar causalidad.

## Entradas

El motor consume exclusivamente las series procesadas generadas por Quality Engine:

```text
quality_interface_processed.csv
quality_probe_processed.csv
```

No modifica los datos crudos ni la configuración de red.

## Requisito de solapamiento temporal

Antes de detectar eventos se calcula la intersección temporal entre interfaz y sondas.

Si no existe solapamiento real:

```text
event_detection_executed = false
event_count = 0
```

y `events.json` conserva una limitación explícita indicando que las series no deben correlacionarse.

Esto evita crear eventos combinando métricas tomadas en momentos distintos.

## Buckets temporales

Por defecto las muestras se agrupan en ventanas de:

```text
bucket_seconds = 5
```

La configuración es externa mediante CLI y puede ajustarse según la frecuencia real de captura.

## Baselines

El detector utiliza medianas sobre la ventana temporal solapada.

Interfaz:

```text
baseline utilization = mediana(utilization_pct)
baseline RX rate      = mediana(rx_rate_mbps)
baseline TX rate      = mediana(tx_rate_mbps)
```

Sondas, por cada target de forma independiente:

```text
baseline RTT          = mediana(rtt_ms)
baseline delay var.   = mediana(delay_variation_ms)
```

Los targets no comparten baseline. Un destino naturalmente lento no oculta un cambio relativo de otro destino.

## Señales del detector

### Utilización

Cuando existe capacidad explícita y Quality Engine pudo calcular utilización:

```text
utilization >= utilization_high_pct
```

o:

```text
utilization - baseline >= utilization_delta_pp
```

Valores por defecto:

```text
utilization_high_pct = 80
utilization_delta_pp = 20
```

### Traffic rate

Si la utilización es N/D porque no existe capacidad autorizada/conocida, el motor puede utilizar un cambio relativo de RX/TX como señal de carga.

Por dirección:

```text
threshold = max(
    baseline * rate_ratio,
    baseline + rate_delta_mbps,
    rate_delta_mbps
)
```

Valores por defecto:

```text
rate_ratio      = 1.75
rate_delta_mbps = 1.0
```

`traffic_rate` no se cuenta simultáneamente con `utilization`, evitando duplicar la misma señal de carga.

## RTT

El RTT se evalúa por target contra su propio baseline:

```text
rtt_threshold = max(
    baseline_rtt * rtt_ratio,
    baseline_rtt + rtt_delta_ms
)
```

Valores por defecto:

```text
rtt_ratio    = 1.5
rtt_delta_ms = 10
```

## Variación temporal de retardo

Se utiliza la serie `delay_variation_ms` producida y documentada por Active Probe Engine / Quality Engine.

```text
delay_threshold = max(
    delay_variation_ms,
    baseline_delay * delay_variation_ratio
)
```

Valores por defecto:

```text
delay_variation_ms    = 10
delay_variation_ratio = 2.0
```

Esta métrica sigue sin presentarse como jitter unidireccional.

## Pérdida / reachability

Un target genera señal cuando:

```text
packet_loss_pct >= 1.0
```

o cuando el ciclo agregado queda sin reachability.

La pérdida se vuelve a derivar de paquetes enviados/recibidos por bucket, evitando depender de promedios de porcentajes.

## Drops y errors

Por bucket se suman los deltas válidos de RX/TX.

Por defecto:

```text
drops_delta  >= 1
errors_delta >= 1
```

## Cuándo existe un evento

Por defecto un bucket se convierte en candidato únicamente cuando cambian al menos dos señales distintas:

```text
min_metrics_changed = 2
```

Ejemplo:

```text
utilization + RTT        -> candidato
RTT + packet_loss        -> candidato
utilization solamente    -> no candidato
```

El umbral es configurable.

## Unión de buckets

Buckets candidatos cercanos se agrupan en un solo evento.

Por defecto:

```text
merge_gap_seconds = 10
```

Así una degradación que permanece varios ciclos no se reporta como decenas de eventos independientes.

## Severidad

La severidad describe únicamente la intensidad del detector según el máximo número de señales simultáneas en un bucket:

```text
2 señales  -> low
3 señales  -> medium
4 señales  -> high
5+ señales -> critical
```

No representa impacto comercial, incumplimiento de SLA ni causa física.

## Confianza

`confidence` expresa confianza en que la ventana merece revisión técnica.

No es confianza causal.

El archivo incluye:

```text
confidence_scope = detección_del_evento_no_causalidad
```

## Campos de cada evento

Cada evento contiene los campos requeridos por el plan:

```text
event_id
start
end
duration_seconds
metrics_changed
severity
evidence
interpretation
hypothesis
confidence
```

`evidence` conserva cada bucket anómalo, timestamp, métricas activadas, baseline, valor observado y threshold utilizado.

## Interpretación e hipótesis

Event Engine solo formula texto prudente y estructural.

Permitido:

> Se observó una ventana con cambios simultáneos en utilización, RTT y pérdida.

No permitido:

> La utilización causó la pérdida.

La hipótesis generada por esta fase indica que el patrón requiere Correlation Engine y Evidence Engine antes de evaluar causas.

## Salidas

```text
events.json
event_timeline.csv
event_summary.csv
```

### `events.json`

Artefacto principal del plan. Contiene metadata, thresholds, limitaciones y evidencia completa.

### `event_timeline.csv`

Serie por bucket utilizada para depuración y trazabilidad.

### `event_summary.csv`

Resumen tabular de eventos para revisión rápida y fases posteriores.

## CLI

Ejemplo con defaults:

```bash
python nettwin.py analyze-events \
  --interface-csv resultados_quality/quality_interface_processed.csv \
  --probe-csv resultados_quality/quality_probe_processed.csv \
  --output resultados_events
```

Los thresholds principales son configurables desde CLI.

## Limitación principal

Event Engine detecta coincidencias temporales. No demuestra causalidad.

Correlation Engine y Evidence Engine serán responsables de contextualizar la asociación, separar hechos de hipótesis y asignar recomendaciones defendibles.
