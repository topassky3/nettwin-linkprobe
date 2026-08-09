from __future__ import annotations

import threading
from typing import Any

import nettwin.orchestrator_engine as _engine
from nettwin.orchestrator_engine import OrchestratorResult
from nettwin.preflight_runtime import load_experiment_config_compatible


_LOADER_LOCK = threading.Lock()


def run_linkprobe(*args: Any, **kwargs: Any) -> OrchestratorResult:
    """Entrada pública del orquestador.

    Reutiliza el scheduler de `orchestrator_engine`, pero garantiza que la carga
    inicial de configuración tenga las mismas propiedades que Fase 11:

    - UTF-8 con o sin BOM;
    - rutas relativas resueltas respecto del archivo original;
    - identidad y SHA-256 de los bytes originales.

    La sustitución del loader se protege con un lock porque una ejecución
    LinkProbe representa un único run de larga duración por proceso.
    """
    with _LOADER_LOCK:
        previous_loader = _engine.load_experiment_config
        _engine.load_experiment_config = load_experiment_config_compatible
        try:
            return _engine.run_linkprobe(*args, **kwargs)
        finally:
            _engine.load_experiment_config = previous_loader
