"""Configuración del motor Qwen 3 / Ollama.

Todo se lee de variables de entorno (o de un archivo .env si python-dotenv
está instalado). Ningún valor vive en el código de los demás módulos.

    OLLAMA_BASE_URL      http://localhost:11434/v1
    OLLAMA_API_KEY       ollama  (Ollama lo ignora, el SDK de OpenAI lo exige)
    OLLAMA_MODEL         qwen3:4b en CI con CPU · qwen3:14b o mayor en local con GPU
    OLLAMA_EMBED_MODEL   bge-m3  (multilingüe; vacío = solo búsqueda léxica)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

try:  # opcional: en CI las variables llegan por el entorno del workflow
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def _s(nombre: str, defecto: str) -> str:
    return os.environ.get(nombre, defecto).strip()


def _i(nombre: str, defecto: int) -> int:
    return int(os.environ.get(nombre, defecto))


def _f(nombre: str, defecto: float) -> float:
    return float(os.environ.get(nombre, defecto))


@dataclass(frozen=True)
class MotorConfig:
    # conexión
    base_url: str = _s("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key: str = _s("OLLAMA_API_KEY", "ollama")
    modelo: str = _s("OLLAMA_MODEL", "qwen3:14b")
    modelo_embeddings: str = _s("OLLAMA_EMBED_MODEL", "bge-m3")

    # robustez
    timeout_s: float = _f("MOTOR_TIMEOUT_S", 600.0)        # por llamada; CPU es lenta
    reintentos: int = _i("MOTOR_REINTENTOS", 3)            # errores de red / 5xx / 429
    espera_base_s: float = _f("MOTOR_ESPERA_BASE_S", 2.0)  # backoff exponencial con jitter
    reparaciones_json: int = _i("MOTOR_REPARACIONES_JSON", 2)

    # muestreo
    temp_extraccion: float = _f("MOTOR_TEMP_EXTRACCION", 0.1)
    temp_sintesis: float = _f("MOTOR_TEMP_SINTESIS", 0.3)
    semilla: int = _i("MOTOR_SEMILLA", 42)
    max_tokens_extraccion: int = _i("MOTOR_MAX_TOKENS_EXTRACCION", 4096)
    max_tokens_sintesis: int = _i("MOTOR_MAX_TOKENS_SINTESIS", 2048)
    # los tokens de razonamiento cuentan contra max_tokens: este extra se suma
    # cuando el razonamiento está encendido para no truncar la respuesta
    max_tokens_razonamiento: int = _i("MOTOR_MAX_TOKENS_RAZONAMIENTO", 4096)

    # razonamiento de Qwen 3 vía /v1 (reasoning_effort). "none" lo apaga.
    esfuerzo_encendido: str = _s("MOTOR_ESFUERZO_RAZONAMIENTO", "medium")
    razonar_extraccion: bool = _s("MOTOR_RAZONAR_EXTRACCION", "0") in ("1", "true", "si", "sí")
    razonar_agente: bool = _s("MOTOR_RAZONAR_AGENTE", "0") in ("1", "true", "si", "sí")

    # salida estructurada: "schema" (json_schema) o "object" (json_object)
    modo_json: str = _s("MOTOR_MODO_JSON", "schema")

    # extracción
    fragmento_chars: int = _i("MOTOR_FRAGMENTO_CHARS", 5000)
    confianza_minima: float = _f("MOTOR_CONFIANZA_MINIMA", 0.5)

    # almacén
    ruta_db: str = _s("MOTOR_DB", "telar.sqlite")
    fragmento_indice_chars: int = _i("MOTOR_FRAGMENTO_INDICE_CHARS", 900)

    # agente
    max_pasos_agente: int = _i("MOTOR_MAX_PASOS", 6)
    resultados_por_busqueda: int = _i("MOTOR_RESULTADOS_BUSQUEDA", 6)

    def con(self, **cambios) -> "MotorConfig":
        """Copia con cambios (útil en pruebas): CFG.con(modelo="qwen3:4b")."""
        return replace(self, **cambios)


CFG = MotorConfig()
