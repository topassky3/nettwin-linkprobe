# Evidence Engine — NetTwin LinkProbe v0.3

## Propósito

Evidence Engine convierte los artefactos de Event Engine y Correlation Engine en hallazgos técnicos trazables y conservadores.

Su estructura sigue el plan del piloto:

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

No es un motor de causalidad y no intenta identificar de forma definitiva la causa física de una degradación.

## Entradas

Entrada obligatoria:

```text
events.json
```

Entrada opcional pero recomendada:

```text
correlations.json
```

La ausencia de `correlations.json` no impide producir un hallazgo a partir de un evento, pero limita su confianza: un evento con confianza alta no se eleva automáticamente a un hallazgo de confianza alta sin soporte correlacional relevante.

## Salidas

```text
evidence_records.json
evidence_summary.csv
evidence_trace.csv
```

### evidence_records.json

Artefacto principal. Conserva metadata, hashes de las fuentes y todos los hallazgos completos.

### evidence_summary.csv

Resumen tabular para revisión humana e incorporación posterior al informe.

### evidence_trace.csv

Tabla de trazabilidad que relaciona cada `FINDING-*` con:

- el `EVENT-*` que lo originó;
- correlaciones usadas como soporte;
- correlaciones usadas únicamente como contexto;
- target;
- número de pares;
- rho de Spearman;
- estado de evidencia.

## Regla de evidencia

Una correlación no se usa automáticamente como soporte de un evento.

Para que una correlación pueda ser soporte fuerte debe:

1. corresponder a una métrica alterada en el evento;
2. corresponder a utilización o contexto de CPU según el tipo de relación;
3. corresponder al target del evento cuando la relación es por target;
4. tener ventana temporal compatible con el evento;
5. tener `evidence_status=descriptive_association`;
6. tener muestras suficientes;
7. superar el umbral `min_support_abs_rho`.

Valor por defecto:

```text
min_support_abs_rho = 0.60
```

## Relaciones de carga que pueden reforzar un evento

```text
utilization ↔ RTT
utilization ↔ delay variation
utilization ↔ packet loss
utilization ↔ drops
```

Las relaciones CPU ↔ RTT y CPU ↔ loss se consideran contexto del host. Pueden orientar una hipótesis o una prueba posterior, pero no demuestran por sí mismas que el host causó el evento.

## Confianza

### ALTA

Se utiliza cuando:

- Event Engine detectó el evento con confianza ALTA; y
- existe al menos una asociación descriptiva positiva fuerte, suficiente, relevante y temporalmente compatible entre utilización y una métrica alterada.

### MEDIA

Se utiliza cuando:

- el evento tiene evidencia suficiente para un hallazgo descriptivo; pero
- no existe soporte correlacional fuerte que justifique elevarlo a ALTA.

Esto incluye el caso donde no se proporciona `correlations.json`.

### BAJA

Se utiliza cuando la confianza del evento es baja y no existe evidencia adicional suficiente.

La confianza se refiere al hallazgo descriptivo, no a certeza causal.

## Hecho

El HECHO se deriva únicamente del evento y conserva:

- ID;
- inicio;
- fin;
- duración;
- métricas alteradas;
- número de buckets anómalos.

Ejemplo:

```text
Entre ... se detectó EVENT-001 durante 15.0 s en 3 buckets anómalos,
con cambios simultáneos en utilización, RTT, pérdida y drops.
```

## Interpretación

Puede afirmar coincidencia temporal y asociación descriptiva.

Permitido:

```text
Se observó degradación temporal coincidente con aumento de carga.
```

No permitido:

```text
La utilización causó definitivamente la pérdida.
```

## Hipótesis

Cuando un evento contiene utilización y degradación de calidad, y existen correlaciones fuertes relevantes, puede producirse una hipótesis como:

```text
Patrón compatible con formación de colas o cuello de botella bajo carga.
```

La hipótesis siempre requiere confirmación.

Si no hay evidencia suficiente, el motor mantiene una hipótesis más general.

## Recomendaciones

Las recomendaciones dependen de la evidencia.

Ejemplo carga-calidad:

```text
Repetir la medición durante hora pico y correlacionar el mismo intervalo
con telemetría del elemento inmediatamente aguas arriba
(utilización, colas y descartes).
```

Ejemplo pérdida sin evidencia de carga:

```text
Repetir con referencias multi-target autorizadas para acotar el dominio afectado.
```

No se generan recomendaciones genéricas únicamente para llenar un reporte.

## Causalidad

Todos los hallazgos incluyen:

```text
causal_claim_allowed = false
```

Y la metadata incluye:

```text
causal_inference_performed = false
```

Correlation Engine aporta asociación descriptiva; Evidence Engine organiza la evidencia y formula hipótesis falsables, pero ninguno demuestra causalidad física.

## Trazabilidad e integridad

Evidence Engine calcula SHA-256 de sus fuentes:

```text
events_source.sha256
correlations_source.sha256
```

Esto permite registrar exactamente qué artefactos dieron origen a los hallazgos.

Los hashes de Fase 8 no sustituyen el `checksums.sha256` global previsto por la fase de integridad; son una capa adicional de trazabilidad.

## Comando

```powershell
python nettwin.py analyze-evidence `
  --events-json resultados_events\events.json `
  --correlations-json resultados_correlations\correlations.json `
  --output resultados_evidence
```

Opciones:

```text
--min-support-rho
--max-correlation-refs
```

## Comportamiento sin correlaciones

```powershell
python nettwin.py analyze-evidence `
  --events-json resultados_events\events.json `
  --output resultados_evidence_solo_eventos
```

El motor conserva el evento como hallazgo, marca correlaciones como N/D y limita la confianza de forma conservadora.

## Comportamiento sin eventos

Si `events.json` contiene una lista vacía:

```text
findings_count = 0
findings = []
```

Evidence Engine no inventa hallazgos para completar el informe.

## Escenario de depuración

```powershell
python scripts\generate_evidence_debug_data.py --output debug_fase8

python nettwin.py analyze-evidence `
  --events-json debug_fase8\events.json `
  --correlations-json debug_fase8\correlations.json `
  --output resultados_fase8_debug
```

Resultado esperado:

```text
FINDING-001 <- EVENT-001
confianza = ALTA
soporte fuerte = 4
```

Las cuatro relaciones fuertes esperadas son:

```text
utilization ↔ RTT
utilization ↔ delay variation
utilization ↔ packet loss
utilization ↔ drops
```

CPU ↔ RTT y CPU ↔ loss son débiles y deben quedar únicamente como contexto.

## Criterios de aceptación de Fase 8

- genera un hallazgo por cada evento válido;
- produce HECHO, INTERPRETACIÓN, HIPÓTESIS, CONFIANZA y RECOMENDACIÓN;
- conserva evidencia del evento;
- enlaza correlaciones relevantes;
- no mezcla targets incompatibles;
- no utiliza correlaciones fuera de la ventana del evento;
- degrada confianza si la evidencia es débil o ausente;
- no inventa hallazgos cuando no hay eventos;
- no afirma causalidad;
- genera hashes SHA-256 de las fuentes;
- produce JSON, resumen CSV y traza CSV;
- el mismo input produce el mismo resultado;
- todas las pruebas anteriores siguen pasando.
