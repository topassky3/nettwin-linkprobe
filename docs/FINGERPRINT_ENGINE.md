# Fase 9 — Event Fingerprint Engine

## Objetivo

Crear una representación compacta, reproducible y trazable de cada evento detectado por NetTwin LinkProbe v0.3.

El objetivo del fingerprint no es explicar causalidad. Su función es resumir la forma del evento para facilitar revisión técnica y, en una fase futura, comparación entre múltiples enlaces.

La versión inicial del vector es:

```text
event-fingerprint-v1
```

## Fuente de datos

El motor consume exclusivamente:

```text
events.json
```

generado por Event Engine.

No vuelve a detectar eventos y no modifica la evidencia original.

## Vector compacto

Cada evento produce como mínimo:

```text
delta_utilization_pp
delta_rtt_pct
delta_rtt_target
delta_delay_variation_pct
delta_delay_variation_target
delta_loss_pp
delta_loss_target
observed_loss_peak_pct
observed_loss_target
delta_drops
duration_seconds
core_completeness_pct
```

Además conserva el detalle por target para RTT, delay variation y packet loss.

## Definiciones

### Δ utilization

```text
Δutilization [pp] = utilization_peak_pct - utilization_baseline_pct
```

Se usa el cambio de mayor magnitud encontrado dentro de la evidencia del evento.

La unidad es puntos porcentuales, no porcentaje relativo.

Ejemplo:

```text
58 % -> 91 %
Δutilization = +33 pp
```

### Δ RTT

Por target:

```text
ΔRTT [%] = ((RTT_peak - RTT_baseline) / RTT_baseline) * 100
```

Solo es calculable si el baseline existe y es distinto de cero.

Ejemplo:

```text
14 ms -> 49 ms
ΔRTT = +250 %
```

Cuando existen múltiples targets, el vector compacto conserva el cambio de mayor magnitud y registra qué target lo produjo. El detalle de los demás targets no se descarta.

### Δ delay variation

Por target:

```text
Δdelay [%] = ((delay_peak - delay_baseline) / delay_baseline) * 100
```

Ejemplo:

```text
2 ms -> 15 ms
Δdelay = +650 %
```

No debe interpretarse como jitter unidireccional; conserva la definición temporal utilizada por Active Probe / Quality Engine.

### Δ packet loss

```text
Δloss [pp] = loss_peak_pct - loss_baseline_pct
```

El baseline debe existir explícitamente en la evidencia o metadata del evento.

Si Event Engine solo conserva el porcentaje observado y no existe baseline:

```text
Δloss = N/D
```

El motor conserva de todas formas:

```text
observed_loss_peak_pct
```

Nunca se asume automáticamente baseline de pérdida igual a 0 %.

### Δ drops

Los drops del Event Engine ya representan deltas de counters por bucket.

Para el fingerprint:

```text
Δdrops = suma de drops_delta de los buckets anómalos del evento
```

Ejemplo:

```text
20 + 27 = 47
```

No representa necesariamente el total histórico del contador, sino los incrementos observados dentro de la ventana del evento.

### Duración

Se conserva exactamente:

```text
duration_seconds
```

reportado por Event Engine.

## Ejemplo objetivo del plan

Entrada conceptual:

```text
EVENT-003

utilization 58 % -> 91 %
RTT         14 ms -> 49 ms
delay var    2 ms -> 15 ms
loss         0 %  -> 1.3 %
drops               +47
duration             446 s
```

Fingerprint esperado:

```text
FP-EVENT-003

Δutilization   +33 pp
ΔRTT           +250 %
Δdelay_var     +650 %
Δloss          +1.3 pp
Δdrops         +47
duration       446 s
```

## Multi-target

Para un evento con varios destinos:

```text
external RTT: +250 %
internal RTT:  +80 %
```

el vector compacto registra:

```text
delta_rtt_pct    = 250
delta_rtt_target = external
```

pero `rtt_by_target` conserva ambos resultados.

Esto evita mezclar destinos y permite auditoría posterior.

## Datos no disponibles

La regla es estricta:

```text
sin baseline -> N/D
```

No se inventan:

- baseline 0;
- capacidad;
- pérdida previa;
- causalidad;
- valores faltantes.

Cada fingerprint incluye `limitations` cuando una métrica esperada no puede expresarse como delta.

## Completitud

El núcleo comparable contiene cinco métricas:

1. Δ utilization;
2. Δ RTT;
3. Δ delay variation;
4. Δ loss;
5. Δ drops.

Se calcula:

```text
core_completeness_pct = métricas disponibles / 5 * 100
```

Esto permite distinguir un fingerprint completo de uno parcial sin rellenar campos artificialmente.

## Trazabilidad

Cada ejecución registra SHA-256 de `events.json`.

Cada fingerprint también obtiene un SHA-256 calculado sobre su representación canónica antes de agregar el propio hash.

Artefactos:

```text
event_fingerprints.json
event_fingerprint_summary.csv
event_fingerprint_trace.csv
```

### event_fingerprints.json

Fuente principal y estructurada.

### event_fingerprint_summary.csv

Una fila por evento con el vector compacto.

### event_fingerprint_trace.csv

Conserva las observaciones que originaron cada delta:

```text
fingerprint_id
event_id
metric
target
timestamp
baseline
observed
delta
unit
```

## Reproducibilidad

Con el mismo `events.json`, misma configuración y misma versión del vector, el resultado debe ser determinista.

La configuración relevante inicial es únicamente:

```text
round_digits
```

El valor por defecto es 3.

## Causalidad

Fingerprint Engine no realiza inferencia causal.

Todos los fingerprints contienen:

```text
causal_interpretation_allowed = false
```

Un fingerprint describe la forma observada de un evento; no demuestra por qué ocurrió.

## CLI

```powershell
python nettwin.py analyze-fingerprints `
  --events-json resultados_fase6\events.json `
  --output resultados_fase9
```

Opcional:

```powershell
--round-digits 3
```

## Dataset sintético

```powershell
python scripts\generate_fingerprint_debug_data.py --output debug_fase9
```

El archivo contiene dos eventos.

`EVENT-003` reproduce exactamente el ejemplo del plan:

```text
Δutilization = +33 pp
ΔRTT         = +250 %
Δdelay       = +650 %
Δloss        = +1.3 pp
Δdrops       = +47
duration     = 446 s
```

`EVENT-004` contiene pérdida observada sin baseline y debe producir:

```text
Δloss = N/D
observed_loss_peak_pct = 2.5
```

## Uso futuro

El plan prevé comparar eventos entre múltiples enlaces.

Para que esa comparación sea técnicamente válida se deberá conservar:

- `vector_version`;
- metodología;
- target;
- disponibilidad de métricas;
- unidad;
- contexto de captura.

Fase 9 prepara esa representación, pero no implementa todavía clustering, similitud automática ni diagnóstico causal entre enlaces.
