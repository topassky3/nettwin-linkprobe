# NetTwin LinkProbe v0.3

NetTwin LinkProbe es un sensor y pipeline de análisis para caracterizar la calidad de un enlace de red durante una ventana observada. Está preparado para el piloto HacheNet de 4 horas y mantiene separados captura, integridad, análisis y reporte.

## Estado

- Versión: `0.3.0`
- Suite validada localmente: `201/201`
- Dry-run local: PASS
- Aceptación local de Fase 15: PASS
- Piloto productivo HacheNet: pendiente de interfaz, targets y autorización reales

## Qué hace

```text
Preflight
  -> Interface Collector + Host Collector + Active Probes
  -> evidencia cruda
  -> sellado SHA-256
  -> Quality Gate
  -> Events / Correlations / Evidence / Fingerprints
  -> Link Health Audit
  -> verificación de integridad posterior
```

El sistema no captura payload de clientes, no inspecciona comunicaciones, no descubre hosts automáticamente y no modifica firewall, routing, interfaces o servicios.

## Requisitos

- Python 3
- Dependencias de `requirements.txt`
- Permiso del administrador para ejecutar el piloto
- Interfaz a observar definida explícitamente
- De 1 a 3 targets previamente autorizados

Instalación:

```powershell
git clone https://github.com/topassky3/nettwin-linkprobe.git
cd nettwin-linkprobe
pip install -r requirements.txt
python nettwin.py --version
```

Esperado:

```text
NetTwin ISP 0.3.0
```

## Para el administrador de HacheNet

La guía operativa está en:

`docs/GUIA_CLIENTE_HACHENET.md`

Flujo resumido:

```powershell
python nettwin.py pilot-config --output hachenet_pilot.json
```

El archivo generado es una plantilla NO ejecutable. Debe completarse únicamente con datos reales autorizados.

Después:

```powershell
python nettwin.py pilot-validate --config hachenet_pilot.json
```

`pilot-validate` no ejecuta probes ni modifica la red. Solo cuando la validación da `PASS` y existe autorización explícita se ejecuta:

```powershell
python nettwin.py pilot-run --config hachenet_pilot.json --authorized
```

El piloto productivo de Fase 15 está diseñado para una ventana de exactamente 4 horas.

## Artefactos principales

Al finalizar correctamente:

```text
pilot_summary.json
run/
  experiment_config.json
  preflight.json
  interface_samples.csv
  host_samples.csv
  probe_samples.csv
  orchestration.json
  run_metadata.json
  checksums.sha256
  integrity_report.json
  logs/linkprobe.log
report/
  dataset_quality.json
  analysis.json
  report.json
  report.html
  analysis/...
```

El directorio `run/` queda sellado y el reporte se genera fuera de él.

## Interpretación responsable

NetTwin describe únicamente la ventana observada. No convierte una ventana de 4 horas en un SLA histórico, no proyecta automáticamente comportamiento semanal/mensual y no presenta correlaciones como causalidad.

Los hallazgos siguen:

```text
HECHO
-> INTERPRETACIÓN
-> HIPÓTESIS
-> CONFIANZA
-> RECOMENDACIÓN
```

## Documentación técnica

- `docs/PILOT_HACHENET.md`
- `docs/PHASE15_ACCEPTANCE.md`
- `docs/PREFLIGHT.md`
- `docs/ORCHESTRATOR.md`
- `docs/REPORTING.md`
- `docs/INTEGRITY_REPRODUCIBILITY.md`

## Desarrollo

Suite completa:

```powershell
python -m unittest discover -s tests -v
```

Estado validado antes de publicar v0.3.0:

```text
Ran 201 tests
OK
```
