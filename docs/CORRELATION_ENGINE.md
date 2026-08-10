# Correlation Engine — Fase 7

El Correlation Engine analiza asociaciones temporales dentro de la ventana observada del piloto NetTwin LinkProbe v0.3.

Su objetivo es cubrir las relaciones del plan:

- `utilization ↔ RTT`;
- `utilization ↔ delay variation`;
- `utilization ↔ packet loss`;
- `utilization ↔ drops`;
- `CPU ↔ RTT`;
- `CPU ↔ loss`.

No diagnostica causalidad.

## Entradas

El motor consume:

```text
quality_interface_processed.csv
quality_probe_processed.csv
host_samples.csv               # opcional para relaciones CPU
```

Los dos primeros provienen del Quality Engine. `host_samples.csv` proviene del Host Collector.

## Alineación temporal

Las series se agrupan en buckets configurables:

```text
bucket_seconds = 5
```

Dentro de cada bucket:

- utilización: mediana;
- RX/TX rate: mediana;
- drops/errors: suma de deltas;
- CPU: mediana;
- RTT: mediana por target;
- delay variation: mediana por target;
- pérdida: se recalcula desde paquetes enviados/recibidos por target.

No se correlacionan muestras de ventanas no solapadas.

Se calculan por separado:

```text
network_overlap    = interface ↔ probes
host_probe_overlap = host ↔ probes
```

Por tanto, un Host Collector ausente o fuera de ventana no invalida las relaciones de interfaz con sondas.

## Correlación de Spearman

Se utiliza correlación de rangos de Spearman:

```text
rho ∈ [-1, 1]
```

Interpretación descriptiva:

```text
|rho| < 0.20        very_weak
0.20 <= |rho| < .40 weak
0.40 <= |rho| < .60 moderate
0.60 <= |rho| < .80 strong
|rho| >= 0.80       very_strong
```

El signo se registra como:

```text
positive
negative
none
```

Una serie constante no recibe un rho artificial:

```text
evidence_status = constant_series
spearman_rho    = N/D
```

## Cantidad de evidencia

Por defecto:

```text
min_pairs = 12
```

Si existen menos pares temporales válidos:

```text
evidence_status          = insufficient_samples
descriptive_significance = insufficient
```

Si hay suficientes pares pero:

```text
|rho| < weak_abs_rho
```

con `weak_abs_rho = 0.30` por defecto:

```text
evidence_status          = weak_association
descriptive_significance = low
```

En asociaciones por encima de ese umbral:

```text
evidence_status          = descriptive_association
descriptive_significance = adequate_descriptive
```

Estos nombres son deliberadamente descriptivos. No equivalen a una prueba inferencial formal.

## Por qué no se reporta un p-value ingenuo

Las muestras de red tomadas cada pocos segundos pueden presentar autocorrelación temporal.

Permutar o tratar cada muestra como independiente puede producir niveles de significancia engañosos.

Por ese motivo, Fase 7 no inventa un p-value inferencial. Registra:

- rho;
- número de pares;
- fuerza;
- suficiencia de muestras;
- asociación descriptiva débil o interpretable;
- limitaciones.

Una fase futura podría incorporar inferencia estadística específica para series temporales si se justifica.

## Relaciones por target

Las relaciones que incluyen sondas se calculan por destino.

Ejemplo:

```text
utilization__rtt__internal
utilization__rtt__controlled_external
utilization__rtt__external_reference
```

Esto evita mezclar un target naturalmente lento con uno rápido.

## Utilización

La correlación con utilización solo se calcula si Quality Engine recibió una capacidad explícita del enlace.

Si:

```text
utilization_pct = N/D
```

el resultado queda:

```text
evidence_status = unavailable
```

El Correlation Engine no utiliza la velocidad reportada por la NIC como capacidad del enlace.

## CPU

`host_samples.csv` es opcional para que el motor pueda seguir calculando relaciones de red cuando no se disponga de host.

Si no se suministra:

```text
CPU ↔ RTT  = N/D
CPU ↔ loss = N/D
```

con una razón explícita.

## Utilización ↔ drops

Esta relación pertenece exclusivamente a la interfaz y puede calcularse aunque las sondas no se solapen, siempre que exista utilización válida.

## Causalidad

Cada fila contiene:

```text
causal_interpretation_allowed = false
```

Un resultado permitido es:

> Durante la ventana observada, utilización y RTT presentaron una asociación monotónica positiva fuerte.

Un resultado no permitido es:

> La alta utilización causó el aumento del RTT.

Para causalidad se requerirían experimentos, temporalidad, controles y evidencia adicional.

## Salidas

```text
correlations.json
correlation_summary.csv
correlation_aligned_pairs.csv
```

### correlations.json

Contiene:

- configuración;
- ventanas y solapamientos;
- targets;
- limitaciones;
- cada relación calculada.

### correlation_summary.csv

Una fila por relación:

```text
relation_id
x_metric
y_metric
target
pair_count
spearman_rho
abs_rho
direction
strength
sufficient_samples
evidence_status
descriptive_significance
first_timestamp
last_timestamp
causal_interpretation_allowed
note
```

### correlation_aligned_pairs.csv

Conserva los buckets alineados usados por el motor para facilitar auditoría y reproducibilidad.

## CLI

```bash
python nettwin.py analyze-correlations \
  --interface-csv runs/quality_interface_processed.csv \
  --probe-csv runs/quality_probe_processed.csv \
  --host-csv runs/host_samples.csv \
  --bucket-seconds 5 \
  --min-pairs 12 \
  --weak-abs-rho 0.30 \
  --output resultados_correlations
```

`--host-csv` puede omitirse.

## Datos sintéticos de depuración

```bash
python scripts/generate_correlation_debug_data.py --output debug_fase7
```

El escenario está diseñado para producir:

```text
utilization ↔ RTT external             rho ≈ +1
utilization ↔ delay variation external rho ≈ +1
utilization ↔ packet loss external     asociación positiva fuerte
utilization ↔ drops                    asociación positiva fuerte
CPU ↔ RTT/loss external                asociación débil
internal                               series constantes => N/D
```

## Alcance

La Fase 7 describe asociaciones de la ventana observada. No demuestra:

- causa física;
- efecto futuro;
- SLA histórico;
- comportamiento semanal/mensual;
- dirección causal;
- impacto comercial.

El Evidence Engine utilizará estos resultados junto con eventos y datos crudos para construir hallazgos defendibles.
