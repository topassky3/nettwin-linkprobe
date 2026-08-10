# Fase 10 — Integridad y reproducibilidad

## Objetivo

Esta fase implementa la sección 19 del plan NetTwin LinkProbe v0.3. Cada ejecución debe poder demostrar qué archivos pertenecían al dataset, qué configuración y versión se usaron y si el mismo dataset produce los mismos resultados agregados al volver a analizarlo.

La fase separa dos propiedades distintas:

1. **Integridad de bytes:** SHA-256 detecta modificación o ausencia de archivos.
2. **Reproducibilidad analítica:** el pipeline se ejecuta dos veces sobre los mismos inputs y se comparan sus agregados con tolerancias numéricas explícitas.

Un checksum válido no demuestra que una medición sea físicamente correcta. Esa evaluación pertenece a calidad de datos y metodología.

## Artefactos

### `run_metadata.json`

Registra como mínimo:

- `schema_version`;
- `run_id`;
- versión del sensor;
- versión del analizador;
- hash y tamaño del archivo de configuración cuando se suministra;
- primer y último timestamp observados;
- duración observada;
- duración solicitada;
- interfaz;
- targets;
- muestras por archivo;
- muestras fallidas cuando existe `sample_status` o `sample_ok`;
- algoritmo de integridad.

La hora de la ejecución se deriva de los timestamps del dataset para que refinalizar un run sin cambios no altere artificialmente el manifiesto.

### `checksums.sha256`

Formato compatible con manifiestos SHA-256:

```text
<64 hex>  ruta/relativa/al/run
```

Las rutas se ordenan lexicográficamente y siempre son relativas al `run_dir`.

Se excluyen únicamente los archivos auto-generados por la propia verificación:

- `checksums.sha256`;
- `integrity_report.json`;
- `reproducibility_report.json`.

`run_metadata.json` **sí está protegido por SHA-256**.

No se admiten symlinks al construir el dataset de integridad.

## Comando `finalize-integrity`

```powershell
python nettwin.py finalize-integrity `
  --run-dir debug_fase10_run `
  --run-id HACHENET-20260809-LINK01-001 `
  --interface eth0 `
  --target external `
  --requested-duration-seconds 115 `
  --config debug_fase10_run\run_config.json
```

Produce:

```text
debug_fase10_run/
├── interface_samples.csv
├── host_samples.csv
├── probe_samples.csv
├── run_config.json
├── run_metadata.json
└── checksums.sha256
```

## Comando `verify-integrity`

```powershell
python nettwin.py verify-integrity --run-dir debug_fase10_run
```

Detecta:

- archivo faltante;
- hash diferente;
- línea inválida en `checksums.sha256`;
- ruta absoluta o que escape del run;
- archivos nuevos no incluidos en el manifiesto.

Por defecto un archivo nuevo se reporta como `untracked` pero no invalida los archivos ya sellados. Para exigir que no exista ningún archivo extra:

```powershell
python nettwin.py verify-integrity `
  --run-dir debug_fase10_run `
  --strict-untracked
```

El resultado se guarda en `integrity_report.json`.

## Reproducibilidad

```powershell
python nettwin.py repro-check `
  --interface-csv debug_fase10_run\interface_samples.csv `
  --probe-csv debug_fase10_run\probe_samples.csv `
  --host-csv debug_fase10_run\host_samples.csv `
  --capacity-mbps 100 `
  --output resultados_fase10_repro
```

Se ejecuta dos veces:

```text
mismo dataset
    │
    ├── replay A
    │   ├── Quality Engine
    │   ├── Event Engine
    │   ├── Correlation Engine
    │   ├── Evidence Engine
    │   └── Fingerprint Engine
    │
    └── replay B
        ├── Quality Engine
        ├── Event Engine
        ├── Correlation Engine
        ├── Evidence Engine
        └── Fingerprint Engine
```

Se comparan semánticamente seis artefactos agregados:

- `quality_interface_summary.csv`;
- `quality_probe_summary.csv`;
- `event_summary.csv`;
- `correlation_summary.csv`;
- `evidence_summary.csv`;
- `event_fingerprint_summary.csv`.

### Tolerancias

Valores por defecto:

```text
absolute tolerance = 1e-9
relative tolerance = 1e-9
```

Los números se comparan con `math.isclose`. Strings, booleanos, `N/D` y estructura se comparan exactamente.

La tolerancia puede configurarse:

```powershell
--abs-tolerance 1e-9
--rel-tolerance 1e-9
```

El reporte `reproducibility_report.json` registra:

- hash de cada input;
- capacidad explícita utilizada;
- tolerancias;
- artefactos comparados;
- SHA-256 de cada replay;
- diferencias encontradas;
- resultado final `reproducible`.

## Propiedades deliberadas

- El NIC reported speed nunca se convierte en capacidad del enlace.
- La capacidad solo se usa si fue suministrada explícitamente a Quality Engine.
- Reproducibilidad no equivale a causalidad ni exactitud física.
- Los checksums no sustituyen las validaciones de calidad temporal.
- La verificación no modifica configuración, rutas, firewall, interfaces ni servicios.
- `run_id`, versión, configuración, targets, interfaz, muestras y duración quedan registradas.

## Criterio de cierre de Fase 10

La fase se considera cerrada cuando:

1. `run_metadata.json` contiene los campos requeridos.
2. `checksums.sha256` se genera en orden estable.
3. Un run intacto verifica como válido.
4. Un archivo modificado se detecta.
5. Un archivo faltante se detecta.
6. Rutas inseguras en el manifiesto se rechazan.
7. Refinalizar el mismo run sin cambios produce el mismo metadata y checksums.
8. El `repro-check` compara los seis agregados.
9. Los dos replays resultan equivalentes con tolerancia `1e-9` por defecto.
10. Todas las pruebas históricas continúan pasando.
