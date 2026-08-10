# Fase 10 — Criterios de aceptación local

La Fase 10 se aprueba únicamente cuando se cumplen todos estos puntos:

- 14/14 pruebas específicas de integridad y reproducibilidad.
- 114/114 pruebas de la suite completa.
- `run_metadata.json` contiene run_id, versiones, configuración, interfaz, targets, duración y conteos.
- `checksums.sha256` verifica el run intacto.
- una modificación deliberada de un archivo produce `Integridad: INVÁLIDA`.
- restaurar/refinalizar el dataset vuelve a producir `Integridad: VÁLIDA`.
- `repro-check` ejecuta dos replays completos.
- se comparan 6 artefactos agregados.
- `reproducibility_report.json` contiene `reproducible: true`.
- las tolerancias por defecto son absolute=1e-9 y relative=1e-9.
- no se infiere causalidad ni exactitud física a partir de checksums o reproducibilidad.

No fusionar la rama hasta completar esta validación local.
