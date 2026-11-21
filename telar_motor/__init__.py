"""Motor de razonamiento, extracción y búsqueda con Qwen 3 vía Ollama.

Uso mínimo::

    from telar_motor import Motor
    m = Motor()                                   # lee OLLAMA_* y MOTOR_* del entorno
    m.ingerir("dec-114", "Decreto 114/2025", texto, url="https://…", tipo="decreto")
    r = m.preguntar("¿Qué empresas contrató la municipalidad y por cuánto?")
    print(r.texto_completo())
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .almacen import Almacen
from .config import CFG, MotorConfig
from .entity_extractor import EntityExtractor, ResultadoExtraccion
from .llm_client import LLMClient
from .search_agent import RespuestaAgente, SearchAgent

__all__ = ["Motor", "MotorConfig", "CFG", "LLMClient", "EntityExtractor", "Almacen", "SearchAgent"]


class Motor:
    """Fachada: une cliente, extractor, almacén y agente con una sola configuración."""

    def __init__(self, cfg: MotorConfig = CFG, cliente: Any | None = None, ruta_db: str | Path | None = None,
                 vectorial: bool = True):
        self.cfg = cfg
        self.llm = LLMClient(cfg, cliente=cliente)
        embedder = self.llm.embeddings if (vectorial and cfg.modelo_embeddings) else None
        self.almacen = Almacen(ruta_db or cfg.ruta_db, embedder=embedder, fragmento_chars=cfg.fragmento_indice_chars)
        self.extractor = EntityExtractor(self.llm, cfg)
        self.agente = SearchAgent(self.llm, self.almacen, cfg)

    def ingerir(self, doc_id: str, titulo: str, texto: str, *, extraer: bool = True, **meta: str) -> ResultadoExtraccion | None:
        """Indexa un documento y, si ``extraer``, guarda sus entidades y relaciones verificadas."""
        self.almacen.agregar_documento(doc_id, titulo, texto, **meta)
        if not extraer:
            return None
        res = self.extractor.extraer(texto, doc_id=doc_id, titulo=titulo)
        self.almacen.guardar_extraccion(res.entidades, res.relaciones)
        return res

    def preguntar(self, pregunta: str) -> RespuestaAgente:
        return self.agente.preguntar(pregunta)

    def exportar_telar(self, destino: str | Path, confianza_minima: float = 0.7) -> dict[str, Any]:
        from .telar_export import exportar

        return exportar(self.almacen, destino, confianza_minima)

    def cerrar(self) -> None:
        self.almacen.cerrar()
