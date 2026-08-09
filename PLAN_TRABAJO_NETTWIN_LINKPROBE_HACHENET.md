# Plan de Trabajo — NetTwin LinkProbe v0.3
## Piloto técnico HacheNet — Auditoría de un enlace durante 4 horas

**Proyecto:** NetTwin ISP / LinkProbe  
**Versión objetivo:** v0.3  
**Cliente piloto:** HacheNet  
**Modalidad inicial:** Piloto sobre un solo enlace  
**Ventana de medición:** 4 horas  
**Objetivo comercial:** demostrar suficiente valor técnico para que HacheNet contrate el análisis o monitoreo de otros enlaces.

---

# 1. Objetivo general

Construir y ejecutar un sensor de medición de red capaz de caracterizar técnicamente el comportamiento de un enlace autorizado de HacheNet durante una ventana de 4 horas, recopilando evidencia reproducible y generando un informe técnico profesional.

El sistema debe permitir:

- medir el comportamiento real del enlace;
- registrar métricas del servidor e interfaz;
- ejecutar sondas activas pequeñas y controladas;
- detectar eventos de degradación;
- relacionar carga con latencia, pérdida, variación de retardo y drops;
- separar hechos, interpretaciones e hipótesis;
- conservar evidencia cruda;
- producir un informe técnico reproducible y defendible.

La meta no es producir un simple dashboard.

La meta es producir **evidencia de ingeniería útil para la toma de decisiones**.

---

# 2. Criterio principal de éxito

El piloto será considerado exitoso si se cumplen simultáneamente los siguientes puntos:

1. El sensor funciona durante la ventana completa sin modificar la configuración productiva del ISP.
2. Se obtiene un dataset íntegro y reproducible.
3. El sistema genera métricas y eventos técnicamente justificables.
4. El informe distingue claramente:
   - hechos;
   - interpretación;
   - hipótesis;
   - nivel de confianza;
   - recomendación.
5. El administrador de HacheNet puede validar o refutar los hallazgos.
6. Al menos un hallazgo aporta información útil que el cliente no tenía presentada previamente de esa manera.
7. El resultado permite plantear de manera natural una fase comercial sobre otros enlaces.

---

# 3. Alcance del piloto

El piloto inicial analizará **un solo enlace** durante aproximadamente cuatro horas.

Este experimento es una:

> Auditoría puntual instrumentada del comportamiento del enlace.

No pretende demostrar con cuatro horas:

- comportamiento semanal;
- crecimiento mensual;
- saturación futura;
- necesidad definitiva de ampliación;
- causa física exacta de todos los eventos;
- capacidad máxima absoluta del enlace.

Esas conclusiones requieren ventanas mayores o experimentos específicos.

---

# 4. Arquitectura objetivo

```text
                       HACHENET
                           │
                     ENLACE PILOTO
                           │
                  ┌────────▼────────┐
                  │ Servidor ISP    │
                  │ autorizado      │
                  └────────┬────────┘
                           │
                    LINKPROBE AGENT
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
     Interface          Active            Host
     Collector          Probes            Health
          │                │                │
       RX/TX              RTT             CPU
       errors             loss            RAM
       drops              delay var.      load
       speed              reachability    uptime
          │                │                │
          └────────────────┼────────────────┘
                           │
                    Measurement Store
                           │
                       raw_samples
                           │
                  ┌────────▼────────┐
                  │ NetTwin Analytics│
                  │      v0.3        │
                  └────────┬─────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      estadística       eventos        correlaciones
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                     Evidence Engine
                           │
                           ▼
                  INFORME TÉCNICO FINAL
```

---

# 5. Principios de diseño

## 5.1 Evidencia antes que conclusiones

Cada conclusión debe poder rastrearse hasta las muestras que la originaron.

Debe conservarse:

- dato crudo;
- timestamp;
- interfaz;
- destino;
- configuración utilizada;
- versión del sensor;
- versión del analizador.

---

## 5.2 No confundir correlación con causalidad

Ejemplo permitido:

> Durante el evento se observó aumento simultáneo de utilización, RTT y pérdida.

Ejemplo no permitido:

> La utilización alta causó definitivamente la pérdida.

Cuando no exista evidencia suficiente, utilizar:

- "compatible con";
- "posible";
- "hipótesis";
- "requiere confirmación".

---

## 5.3 Carga mínima sobre producción

El sensor debe ser principalmente observacional.

Las pruebas activas deberán:

- ser pequeñas;
- ser configurables;
- estar previamente autorizadas;
- evitar saturar el enlace;
- evitar pruebas agresivas de throughput salvo autorización explícita.

---

# 6. Datos que debe capturar el sensor

## 6.1 Interfaz de red

Registrar como mínimo:

```text
timestamp
interface
rx_bytes
tx_bytes
rx_packets
tx_packets
rx_errors
tx_errors
rx_drops
tx_drops
interface_state
reported_link_speed
mtu
```

Cuando alguna métrica no esté disponible, debe quedar explícitamente registrada como no disponible.

---

## 6.2 Estado del servidor

Registrar:

```text
cpu_percent
memory_percent
load_average
uptime
```

Objetivo:

diferenciar degradación de red de saturación del host donde se ejecuta el sensor.

---

## 6.3 Sondas activas

Registrar por destino:

```text
timestamp
target
rtt
packet_loss
delay_variation
reachability
probe_type
```

Debe documentarse exactamente cómo se calcula cada métrica.

---

# 7. Estrategia multi-target

No debe medirse únicamente un destino arbitrario de Internet.

Idealmente se utilizarán hasta tres referencias:

```text
LinkProbe
   │
   ├── Gateway / punto interno autorizado
   │
   ├── Punto externo controlado
   │
   └── Referencia pública externa
```

Esto permite comparar si una degradación aparece:

- cerca del servidor;
- dentro del dominio controlado;
- únicamente hacia Internet externo.

Los destinos definitivos deben ser aprobados por HacheNet.

---

# 8. Estructura de almacenamiento

Debe existir almacenamiento de datos crudos.

Propuesta mínima:

```text
data/
├── run_metadata.json
├── preflight.json
├── interface_samples.csv
├── host_samples.csv
├── probe_samples.csv
├── events.json
├── analysis.json
└── checksums.sha256
```

Cada ejecución tendrá un identificador único.

Ejemplo:

```text
run_id = HACHENET-20260809-LINK01-001
```

---

# 9. Preflight obligatorio

Antes de iniciar las cuatro horas debe ejecutarse un diagnóstico previo.

El preflight debe registrar:

- sistema operativo;
- hostname anonimizable;
- hora del sistema;
- zona horaria;
- interfaz seleccionada;
- IP local cuando sea necesario;
- gateway;
- MTU;
- velocidad reportada;
- permisos disponibles;
- herramientas necesarias;
- destinos de sondas;
- espacio en disco;
- versión del sensor;
- configuración del experimento.

## Criterio de aceptación

Debe generarse:

```text
preflight.json
```

sin modificar:

- rutas;
- firewall;
- interfaces;
- servicios;
- configuración de red.

---

# 10. Fases de desarrollo

## Fase 0 — Congelar NetTwin v0.2.2

### Trabajo

Mantener intacto el analizador existente como baseline.

### Criterios de aceptación

- Todas las pruebas existentes siguen pasando.
- No se rompe compatibilidad con análisis histórico.
- Se etiqueta una versión estable.

---

## Fase 1 — Crear modo `link-audit`

Agregar un modo específico para ventanas cortas.

Ejemplo conceptual:

```bash
nettwin link-audit
```

Debe diferenciarse de:

```bash
nettwin analyze-history
```

### `analyze-history`

Orientado a:

- 7 días;
- 30 días;
- tendencias;
- crecimiento;
- planificación de capacidad.

### `link-audit`

Orientado a:

- horas;
- estabilidad;
- anomalías;
- relación carga-calidad;
- evidencia temporal.

### Criterio de aceptación

Ninguna proyección de largo plazo se ejecuta automáticamente en modo `link-audit`.

---

# 11. Interface Collector

Crear módulo para observación continua de interfaz.

Frecuencia configurable.

Ejemplo:

```yaml
interface_interval_seconds: 5
```

## Criterios de aceptación

- Captura continua durante toda la ejecución.
- Maneja reinicio de contadores.
- Detecta overflow si aplica.
- No produce valores negativos incorrectos.
- Registra muestras faltantes.
- No detiene todo el sensor ante una lectura puntual fallida.

---

# 12. Host Collector

Registrar:

- CPU;
- RAM;
- load average;
- uptime.

## Criterio de aceptación

Debe ser posible observar si un evento de red coincide con saturación significativa del host.

---

# 13. Active Probe Engine

Crear motor de sondas.

Debe medir:

- RTT;
- reachability;
- pérdida;
- variación de retardo.

La configuración debe permitir:

```yaml
probe_interval_seconds: 5

targets:
  - name: internal
    address: X.X.X.X

  - name: controlled_external
    address: X.X.X.X

  - name: external_reference
    address: X.X.X.X
```

Los valores reales serán definidos con el administrador.

## Criterios de aceptación

- Las sondas generan tráfico mínimo.
- El volumen estimado de tráfico queda documentado.
- Cada resultado contiene timestamp y target.
- Un destino caído no detiene los demás collectors.

---

# 14. Quality Engine

Calcular como mínimo:

## Latencia

```text
minimum
median
p95
p99
maximum
```

## Pérdida

```text
total_probes
successful_probes
lost_probes
loss_percent
```

## Variación temporal

Documentar exactamente la metodología utilizada.

## Interfaz

```text
rx_rate
tx_rate
errors_delta
drops_delta
utilization_when_capacity_known
```

## Disponibilidad observada

Calcular únicamente sobre la ventana de medición.

Nunca presentar cuatro horas como disponibilidad histórica del servicio.

---

# 15. Event Engine

El sistema debe detectar ventanas donde cambien varias métricas.

Ejemplo conceptual:

```text
EVENT-003

start: 17:14:22
end:   17:21:48

utilization:
58% -> 91%

RTT:
14 ms -> 49 ms

delay_variation:
2 ms -> 15 ms

packet_loss:
0.0% -> 1.3%

drops:
0 -> 47
```

Cada evento debe incluir:

```text
event_id
start
end
duration
metrics_changed
severity
evidence
interpretation
hypothesis
confidence
```

---

# 16. Correlation Engine

Analizar relaciones entre:

```text
utilization ↔ RTT
utilization ↔ delay variation
utilization ↔ packet loss
utilization ↔ drops
CPU ↔ RTT
CPU ↔ loss
```

Cuando haya poca cantidad de muestras o baja significancia, debe indicarse.

No convertir una correlación en afirmación causal.

---

# 17. Evidence Engine

Esta es una de las piezas más importantes.

Cada hallazgo debe tener la estructura:

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

Ejemplo:

```text
HECHO

Entre 17:14 y 17:21 se observó un incremento
simultáneo de utilización, RTT, variación de
retardo y pérdida.

INTERPRETACIÓN

Existe degradación temporal coincidente con
el aumento de carga.

HIPÓTESIS

Posible formación de colas o cuello de
botella durante la ventana observada.

CONFIANZA

MEDIA

RECOMENDACIÓN

Repetir medición durante hora pico y
correlacionar con telemetría del elemento
inmediatamente aguas arriba.
```

---

# 18. Fingerprint de eventos

Crear una representación compacta por evento.

Ejemplo:

```text
EVENT-003

Δutilization   +33 pp
ΔRTT           +250 %
Δdelay_var     +650 %
Δloss          +1.3 pp
Δdrops         +47

duration       446 s
```

Objetivo futuro:

permitir comparar eventos entre múltiples enlaces.

---

# 19. Integridad y reproducibilidad

Toda ejecución deberá registrar:

- versión;
- configuración;
- hora;
- run_id;
- checksums;
- número de muestras;
- muestras fallidas;
- targets;
- interfaz;
- duración.

Debe generarse un archivo:

```text
checksums.sha256
```

## Criterio de aceptación

El mismo dataset analizado nuevamente debe producir los mismos valores agregados dentro de tolerancias numéricas documentadas.

---

# 20. Experimento de 4 horas

## Secuencia

```text
T0
│
├── Preflight
│
├── inicio collectors
│
├──────────── Hora 1
│
├──────────── Hora 2
│
├──────────── Hora 3
│
├──────────── Hora 4
│
├── detener collectors
│
├── validar integridad
│
├── generar checksums
│
└── ejecutar análisis
T+4h
```

La ejecución debe ser automática una vez iniciada.

---

# 21. Criterios mínimos de calidad de datos

Antes de generar conclusiones debe verificarse:

- porcentaje de muestras válidas;
- huecos temporales;
- timestamps fuera de orden;
- duplicados;
- reloj del host;
- reinicios de counters;
- valores imposibles;
- target inaccesible;
- interfaz incorrecta.

El informe debe mostrar la calidad del dataset.

Ejemplo:

```text
Muestras esperadas:     2880
Muestras recibidas:     2877
Integridad temporal:    99.90 %
Huecos detectados:      1
```

---

# 22. Informe técnico final

El informe debe ser comprensible tanto para:

- administrador técnico;
- propietario;
- persona encargada de decisión comercial.

## Portada

```text
HACHENET

LINK HEALTH AUDIT

Enlace: LINK-01
Ventana: 4 h
Sensor: NetTwin LinkProbe v0.3
```

---

# 23. Resumen ejecutivo

Una página máximo.

Debe responder:

- qué se midió;
- cuándo;
- cuántas muestras;
- resultado general;
- eventos principales;
- limitaciones;
- recomendación.

Ejemplo:

```text
Durante cuatro horas se realizó una auditoría
instrumentada del enlace LINK-01.

Se recolectaron X muestras válidas.

Se identificaron N eventos relevantes.

El evento de mayor interés ocurrió entre
XX:XX y XX:XX y presentó cambios simultáneos
en utilización, RTT, variación de retardo
y pérdida.

Los resultados justifican una ventana
adicional de medición para confirmar
el comportamiento durante otros periodos
de carga.
```

---

# 24. Tabla principal de resultados

Ejemplo:

```text
Disponibilidad observada    99.xx %
RTT mediano                 xx ms
RTT P95                     xx ms
RTT P99                     xx ms
Pérdida observada           x.xx %
Delay variation P95         xx ms
Utilización media           xx %
Utilización P95             xx %
Utilización máxima          xx %
RX drops                    xxx
TX drops                    xxx
Eventos relevantes          x
```

Los campos no medibles deben indicarse como:

```text
N/D
```

Nunca inventar valores.

---

# 25. Línea temporal maestra

El informe deberá incluir gráficos sincronizados de:

```text
utilización
RTT
delay variation
packet loss
drops
CPU
```

Objetivo:

permitir observar si diferentes métricas cambian simultáneamente.

---

# 26. Sección de eventos

Cada evento relevante tendrá:

- ID;
- inicio;
- fin;
- duración;
- gráfica;
- métricas;
- evidencia;
- interpretación;
- hipótesis;
- confianza;
- recomendación.

---

# 27. Metodología

El informe deberá explicar:

- dónde estuvo el sensor;
- interfaz observada;
- frecuencia;
- targets;
- metodología de RTT;
- metodología de pérdida;
- metodología de variación de retardo;
- metodología de utilización;
- limitaciones.

Debe ser posible que otro ingeniero entienda cómo se obtuvieron los resultados.

---

# 28. Limitaciones

El informe debe declarar explícitamente:

> Una ventana de cuatro horas caracteriza únicamente el periodo observado y no representa necesariamente el comportamiento semanal o mensual del enlace.

También deberá señalar:

- limitaciones del host;
- limitaciones del destino;
- métricas no disponibles;
- posibles factores externos;
- imposibilidad de inferir causa física cuando no haya evidencia.

---

# 29. Recomendaciones

Las recomendaciones deberán estar directamente relacionadas con evidencia.

Ejemplo:

```text
Evidencia:
aumento repetido de RTT bajo alta utilización.

Recomendación:
repetir medición durante hora pico y obtener
telemetría del elemento aguas arriba.
```

No generar recomendaciones genéricas únicamente para llenar el informe.

---

# 30. Próxima fase comercial

El informe podrá sugerir una siguiente etapa.

## Piloto

```text
1 enlace
4 horas
auditoría puntual
```

## Fase comercial 1

```text
varios enlaces
7 días
comparación entre enlaces
```

## Fase comercial 2

```text
monitoreo continuo
alertas
tendencias
capacidad
```

## Fase comercial 3

```text
Network Intelligence
comparación histórica
predicción
planificación
```

La recomendación comercial debe aparecer únicamente después de los resultados técnicos.

---

# 31. Seguridad y privacidad

El sensor NO deberá:

- capturar payload;
- inspeccionar comunicaciones de clientes;
- recolectar navegación;
- almacenar contenido;
- realizar escaneo indiscriminado;
- descubrir equipos no autorizados;
- modificar configuración de red;
- modificar firewall;
- modificar routing;
- abrir servicios innecesarios;
- ejecutar explotación;
- ejecutar pruebas agresivas sin autorización.

Debe limitarse al servidor, interfaz, destinos y pruebas previamente autorizados.

---

# 32. Requisitos operacionales

El programador debe procurar:

- consumo bajo de CPU;
- consumo bajo de RAM;
- bajo uso de disco;
- tráfico activo mínimo;
- manejo de señales de terminación;
- cierre limpio;
- logs claros;
- recuperación ante errores parciales.

---

# 33. Configuración externa

Evitar constantes importantes dentro del código.

Ejemplo:

```yaml
run:
  duration_hours: 4

interface:
  name: eth0
  interval_seconds: 5

host:
  interval_seconds: 5

probes:
  interval_seconds: 5

targets:
  - name: gateway
    address: 192.0.2.1

output:
  directory: ./runs
```

Las direcciones anteriores son solamente ejemplos.

---

# 34. Logging

Crear:

```text
logs/
└── linkprobe.log
```

Registrar:

- inicio;
- configuración;
- collectors activos;
- errores;
- target inaccesible;
- muestras descartadas;
- finalización;
- checksums.

No registrar información sensible innecesaria.

---

# 35. CLI propuesta

Ejemplo:

```bash
nettwin preflight --config config.yaml
```

```bash
nettwin link-audit --config config.yaml
```

```bash
nettwin analyze --run RUN_ID
```

```bash
nettwin report --run RUN_ID
```

Opcional:

```bash
nettwin verify --run RUN_ID
```

---

# 36. Dry-run obligatorio antes de HacheNet

Antes de instalar en producción se debe probar el pipeline completo.

El dry-run debe demostrar:

```text
sensor
↓
captura
↓
almacenamiento
↓
validación
↓
eventos
↓
análisis
↓
informe
```

## Criterio de aceptación

El equipo debe poder ejecutar una simulación o prueba local sin intervención manual durante el proceso.

---

# 37. Pruebas mínimas

Crear pruebas para:

- percentiles;
- pérdida;
- counters;
- counter reset;
- timestamps;
- samples faltantes;
- disponibilidad;
- event detection;
- correlation engine;
- Evidence Engine;
- checksum;
- reproducibilidad;
- generación de informe.

Las pruebas existentes del proyecto deben continuar pasando.

---

# 38. Entregables del programador

Al terminar, deben existir:

```text
README.md
ARCHITECTURE.md
SECURITY.md
METHODOLOGY.md
CONFIGURATION.md

src/
tests/
config.example.yaml

runs/
reports/
```

Y comandos claros para:

```text
preflight
ejecución
análisis
verificación
reporte
```

---

# 39. Definition of Done — Sensor

El sensor estará terminado cuando:

- [ ] puede ejecutarse con una configuración externa;
- [ ] identifica la interfaz seleccionada;
- [ ] captura RX/TX;
- [ ] captura errors/drops;
- [ ] captura métricas del host;
- [ ] ejecuta sondas autorizadas;
- [ ] registra RTT;
- [ ] registra pérdida;
- [ ] registra variación de retardo;
- [ ] conserva timestamps;
- [ ] conserva datos crudos;
- [ ] maneja errores parciales;
- [ ] genera metadata;
- [ ] genera checksums;
- [ ] se detiene limpiamente;
- [ ] no modifica configuración de red.

---

# 40. Definition of Done — Analytics

- [ ] calcula mediana;
- [ ] calcula P95;
- [ ] calcula P99;
- [ ] calcula pérdida;
- [ ] calcula utilización cuando sea posible;
- [ ] calcula deltas de drops/errors;
- [ ] detecta eventos;
- [ ] calcula correlaciones;
- [ ] no confunde correlación con causalidad;
- [ ] genera evidence records;
- [ ] genera fingerprints;
- [ ] documenta limitaciones.

---

# 41. Definition of Done — Informe

- [ ] portada;
- [ ] resumen ejecutivo;
- [ ] metodología;
- [ ] calidad del dataset;
- [ ] tabla de métricas;
- [ ] línea temporal;
- [ ] eventos relevantes;
- [ ] hechos;
- [ ] interpretaciones;
- [ ] hipótesis;
- [ ] confianza;
- [ ] recomendaciones;
- [ ] limitaciones;
- [ ] anexos técnicos;
- [ ] versión HTML;
- [ ] versión PDF si el pipeline lo soporta.

---

# 42. Criterio comercial final

El objetivo comercial NO será:

> producir un PDF bonito.

El objetivo será:

> demostrar mediante evidencia técnica que NetTwin LinkProbe puede revelar información útil sobre el comportamiento de un enlace y justificar que HacheNet quiera aplicar el mismo análisis a otros enlaces.

La pregunta final que debe poder responder el cliente es:

> ¿La información obtenida justifica continuar midiendo otros enlaces?

Si la respuesta es sí, el piloto habrá cumplido su misión.

---

# 43. Prioridades

Orden estricto:

```text
1. Integridad de datos
2. Seguridad
3. Reproducibilidad
4. Calidad de medición
5. Evidencia
6. Diagnóstico
7. Informe
8. Automatización
9. Escalabilidad
10. Inteligencia avanzada
```

No invertir tiempo inicialmente en:

- IA generativa;
- dashboard complejo;
- frontend grande;
- predicción sofisticada;
- descubrimiento automático de toda la red.

Primero demostrar que sabemos **medir bien**.

---

# 44. Resultado esperado

Al finalizar el piloto deberemos disponer de:

```text
NetTwin LinkProbe v0.3
        +
dataset real
        +
metodología reproducible
        +
eventos verificables
        +
informe profesional
        ↓
primera evidencia comercial
        ↓
propuesta pagada para otros enlaces
```

---

## Estado objetivo

**NetTwin deja de ser solamente un analizador de CSV y se convierte en un sistema real de medición y diagnóstico de enlaces.**
