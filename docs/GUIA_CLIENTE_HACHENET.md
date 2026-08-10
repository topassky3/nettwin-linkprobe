# Guía del cliente — Piloto HacheNet de NetTwin LinkProbe v0.3

Esta guía está pensada para el administrador que ejecutará el piloto. El objetivo es medir un enlace autorizado durante 4 horas, generar evidencia reproducible y devolver el paquete de resultados para análisis/revisión.

## 1. Lo que necesitamos antes de ejecutar

HacheNet debe definir explícitamente:

1. **Interfaz del servidor que corresponde al enlace a observar**.
2. **Uno a tres targets autorizados** para las sondas ICMP.
3. **Rol técnico de cada target**, por ejemplo referencia interna, referencia controlada o referencia externa.
4. **Responsable que aprueba esos targets**.
5. **Referencia de autorización**: ticket, correo, reunión, cambio aprobado u otra referencia interna verificable.
6. **Capacidad real del enlace**, únicamente si se conoce. Si no se conoce, se deja `null`; NetTwin no sustituye ese dato con la velocidad de la NIC.

No se requiere entregar credenciales, tráfico de clientes ni contenido de comunicaciones.

## 2. Preparar el software

En el servidor autorizado:

```bash
git clone https://github.com/topassky3/nettwin-linkprobe.git
cd nettwin-linkprobe
python -m pip install -r requirements.txt
python nettwin.py --version
```

Debe mostrar versión `0.3.0`.

## 3. Crear la plantilla

```bash
python nettwin.py pilot-config --output hachenet_pilot.json
```

La plantilla nace bloqueada deliberadamente. No ejecutar todavía `pilot-run`.

## 4. Completar `hachenet_pilot.json`

Reemplazar únicamente con información real y aprobada:

```json
{
  "run": {
    "run_id": "HACHENET-AAAAMMDD-LINK01-001",
    "duration_seconds": 14400
  },
  "interface": {
    "name": "INTERFAZ_REAL",
    "interval_seconds": 5,
    "capacity_mbps": null
  },
  "probes": {
    "interval_seconds": 5,
    "timeout_ms": 1000,
    "payload_bytes": 32,
    "count_per_target": 1
  },
  "targets": [
    {
      "name": "NOMBRE_TARGET",
      "address": "IP_AUTORIZADA",
      "role": "ROL_TECNICO",
      "authorized": true
    }
  ],
  "pilot": {
    "authorization": {
      "confirmed": true,
      "reference": "REFERENCIA_REAL",
      "approved_by": "RESPONSABLE_REAL"
    }
  }
}
```

No cambiar `duration_seconds`: el piloto productivo de Fase 15 debe durar `14400` segundos = 4 horas.

## 5. Validar SIN ejecutar probes

Antes del piloto:

```bash
python nettwin.py pilot-validate --config hachenet_pilot.json
```

Este comando es de preparación. No ejecuta sondas activas y no modifica configuración de red.

El administrador debe revisar:

```text
Estado: PASS
Duración: 4 h
Número de targets: 1–3
Carga ICMP estimada
Filas esperadas
Warnings
```

Si aparece `FAIL`, **no ejecutar el piloto**. Corregir la configuración y volver a validar.

## 6. Condiciones de seguridad

NetTwin bloquea o evita por diseño:

- targets no marcados como autorizados;
- loopback en modo productivo;
- multicast y link-local;
- redes reservadas para documentación;
- más de 3 targets;
- payload ICMP > 128 bytes;
- más de 3 echoes por target/ciclo;
- intervalo de probes < 2 segundos;
- duración productiva distinta de 4 horas.

NetTwin no:

- captura payload de usuarios;
- inspecciona conversaciones o navegación;
- escanea rangos de red;
- descubre hosts automáticamente;
- cambia firewall;
- cambia routing;
- cambia configuración de interfaces;
- ejecuta pruebas agresivas de throughput.

## 7. Ejecutar las 4 horas

Solo después de que `pilot-validate` indique `PASS` y el administrador confirme que la configuración corresponde al enlace/targets autorizados:

```bash
python nettwin.py pilot-run --config hachenet_pilot.json --authorized
```

Después de iniciar, el proceso es automático:

```text
validación
-> preflight
-> captura coordinada
-> sellado SHA-256
-> verificación estricta
-> análisis
-> informe
-> segunda verificación estricta
```

No es necesario ejecutar comandos analíticos adicionales durante la ventana.

## 8. Qué debe verse al terminar

Resultado esperado:

```text
Estado: PASS
Quality Gate: PASS
Análisis ejecutado: SÍ
```

El archivo `pilot_summary.json` debe mostrar las etapas:

```text
validation              PASS
preflight_capture       PASS
integrity_before_report PASS
analysis_report         PASS
integrity_after_report  PASS
```

## 9. Qué archivos conservar

No modificar manualmente los archivos dentro de `run/`.

Conservar completos:

```text
hachenet_pilot.json
pilot_summary.json
run/
report/
```

Especialmente:

```text
run/checksums.sha256
run/run_metadata.json
run/interface_samples.csv
run/host_samples.csv
run/probe_samples.csv
report/report.html
report/report.json
report/analysis.json
```

## 10. Verificación final opcional

Antes de entregar el paquete:

```bash
python nettwin.py verify-integrity --run-dir run --strict-untracked
```

Esperado:

```text
Integridad: VÁLIDA
Faltantes: 0
Modificados: 0
Rutas inseguras: 0
No rastreados: 0
```

## 11. Qué debe devolver HacheNet

Entregar el directorio del piloto completo, sin editar los CSV ni el manifiesto SHA-256:

```text
hachenet_pilot.json
pilot_summary.json
run/
report/
```

El `report.html` ya contiene el Link Health Audit generado automáticamente. El análisis posterior puede revisar eventos, correlaciones, Evidence Records y fingerprints conservando la evidencia original intacta.

## 12. Cómo interpretar el resultado

El piloto caracteriza **solo esas 4 horas observadas**.

No debe interpretarse automáticamente como:

- SLA histórico;
- disponibilidad mensual;
- comportamiento semanal;
- crecimiento futuro;
- capacidad máxima física;
- causa definitiva de una degradación.

Cuando NetTwin detecta algo, mantiene esta separación:

```text
HECHO
-> INTERPRETACIÓN
-> HIPÓTESIS
-> CONFIANZA
-> RECOMENDACIÓN
```

Una correlación temporal no se presenta como prueba de causalidad.

## Resumen para el administrador

```text
1. Instalar dependencias.
2. Crear hachenet_pilot.json.
3. Completar interfaz + targets + autorización reales.
4. Ejecutar pilot-validate.
5. Si PASS, revisar estimación de carga.
6. Ejecutar pilot-run --authorized.
7. Dejar terminar las 4 horas.
8. Confirmar PASS e integridad válida.
9. No modificar run/.
10. Entregar hachenet_pilot.json + pilot_summary.json + run/ + report/.
```
