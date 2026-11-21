"""Cliente de Qwen 3 vía la API compatible con OpenAI de Ollama.

Decisiones tomadas a partir de la documentación de Ollama
(https://docs.ollama.com/api/openai-compatibility) y del issue #17969:

* El razonamiento de Qwen 3 se apaga con ``reasoning_effort: "none"``. Se envía
  por ``extra_body`` porque Ollama descarta en silencio ``think`` en /v1 y el
  sufijo ``/no_think`` no tiene efecto en versiones recientes.
* El razonamiento llega en ``message.reasoning``, separado de ``content``.
  Igual se limpian etiquetas ``<think>`` por si un modelo las deja en el texto.
* Los tokens de razonamiento cuentan contra ``max_tokens``: si la respuesta
  termina por ``length`` con ``content`` vacío, se reintenta con más margen.
* ``tool_choice``, ``n``, ``logit_bias`` y ``user`` no están soportados: nunca
  se envían.
* Ollama no garantiza que la salida estructurada cumpla el esquema, así que
  todo JSON se valida con Pydantic y, si falla, se pide una reparación
  mostrando los errores al modelo.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from .config import CFG, MotorConfig

log = logging.getLogger("telar_motor.llm")

Modo = Literal["extraccion", "sintesis"]
T = TypeVar("T", bound=BaseModel)

_THINK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


class LLMError(RuntimeError):
    """Fallo definitivo del motor (tras agotar reintentos)."""


class LLMTruncado(LLMError):
    """La respuesta se cortó por max_tokens sin dejar contenido útil."""


class SalidaInvalida(LLMError):
    """El modelo no produjo JSON válido para el esquema tras las reparaciones."""

    def __init__(self, mensaje: str, ultimo_texto: str, errores: str):
        super().__init__(mensaje)
        self.ultimo_texto = ultimo_texto
        self.errores = errores


@dataclass
class LlamadaHerramienta:
    id: str
    nombre: str
    argumentos_crudos: str

    def argumentos(self) -> dict[str, Any]:
        try:
            v = json.loads(self.argumentos_crudos or "{}")
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass
class RespuestaLLM:
    contenido: str
    razonamiento: str | None
    herramientas: list[LlamadaHerramienta]
    fin: str | None
    uso: dict[str, int]
    segundos: float
    modelo: str
    mensaje_asistente: dict[str, Any] = field(default_factory=dict)


def _limpiar(texto: str | None) -> str:
    return _THINK.sub("", texto or "").strip()


def extraer_json(texto: str) -> str:
    """Quita cercas ```json y recorta al primer objeto JSON balanceado."""
    t = _FENCE.sub("", _limpiar(texto))
    ini = t.find("{")
    if ini < 0:
        return t
    prof, en_cadena, escape = 0, False, False
    for i in range(ini, len(t)):
        c = t[i]
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c == "{":
            prof += 1
        elif c == "}":
            prof -= 1
            if prof == 0:
                return t[ini : i + 1]
    return t[ini:]


class LLMClient:
    """Envoltura delgada y robusta sobre ``openai.OpenAI`` apuntando a Ollama.

    ``cliente`` permite inyectar un doble de pruebas con la misma interfaz
    (``chat.completions.create`` y ``embeddings.create``).
    """

    def __init__(self, cfg: MotorConfig = CFG, cliente: Any | None = None):
        self.cfg = cfg
        if cliente is None:
            from openai import OpenAI  # import diferido: las pruebas no lo necesitan

            cliente = OpenAI(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout=cfg.timeout_s,
                max_retries=0,  # los reintentos se gestionan aquí, con backoff propio
            )
        self._c = cliente
        self._sin_reasoning_effort = False  # Ollama antiguo que rechaza el campo
        self._modo_json = cfg.modo_json

    # ───────────────────────── núcleo ─────────────────────────

    def chat(
        self,
        mensajes: Sequence[dict[str, Any]],
        *,
        modo: Modo = "sintesis",
        razonar: bool = False,
        herramientas: list[dict[str, Any]] | None = None,
        formato: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperatura: float | None = None,
    ) -> RespuestaLLM:
        cfg = self.cfg
        temp = temperatura if temperatura is not None else (
            cfg.temp_extraccion if modo == "extraccion" else cfg.temp_sintesis
        )
        tope = max_tokens or (cfg.max_tokens_extraccion if modo == "extraccion" else cfg.max_tokens_sintesis)
        if razonar:
            tope += cfg.max_tokens_razonamiento

        params: dict[str, Any] = {
            "model": cfg.modelo,
            "messages": list(mensajes),
            "temperature": temp,
            "seed": cfg.semilla,
            "max_tokens": tope,
        }
        if herramientas:
            params["tools"] = herramientas  # sin tool_choice: Ollama no lo soporta
        if formato:
            params["response_format"] = formato
        if not self._sin_reasoning_effort:
            params["extra_body"] = {"reasoning_effort": cfg.esfuerzo_encendido if razonar else "none"}

        t0 = time.monotonic()
        r = self._normalizar(self._crear_con_reintentos(params), t0)

        if r.fin == "length" and not r.contenido and not r.herramientas:
            # el razonamiento se comió el presupuesto: un intento más con el doble
            log.warning("Respuesta truncada sin contenido (max_tokens=%s); reintentando con más margen", tope)
            params["max_tokens"] = tope * 2
            r = self._normalizar(self._crear_con_reintentos(params), t0)
            if r.fin == "length" and not r.contenido and not r.herramientas:
                raise LLMTruncado(f"El modelo agotó {tope * 2} tokens sin producir contenido")
        return r

    def estructurado(
        self,
        mensajes: Sequence[dict[str, Any]],
        esquema: type[T],
        *,
        modo: Modo = "extraccion",
        razonar: bool = False,
        nombre: str | None = None,
    ) -> tuple[T, RespuestaLLM]:
        """Pide JSON conforme a ``esquema`` y lo valida; repara si hace falta."""
        historial = list(mensajes)
        ultimo, errores = "", ""
        for intento in range(self.cfg.reparaciones_json + 1):
            r = self.chat(historial, modo=modo, razonar=razonar, formato=self._formato(esquema, nombre))
            ultimo = r.contenido
            try:
                return esquema.model_validate_json(extraer_json(r.contenido)), r
            except ValidationError as e:
                errores = _resumir_errores(e)
            except ValueError as e:  # JSON mal formado
                errores = str(e)
            log.info("Salida inválida (intento %s): %s", intento + 1, errores[:300])
            historial = list(mensajes) + [
                {"role": "assistant", "content": r.contenido[:6000]},
                {
                    "role": "user",
                    "content": (
                        "Tu respuesta no cumple el esquema JSON. Errores:\n"
                        f"{errores}\n"
                        "Devuelve SOLO el objeto JSON corregido, sin texto adicional "
                        "y sin inventar datos que no estén en el texto original."
                    ),
                },
            ]
        raise SalidaInvalida("El modelo no produjo JSON válido para el esquema", ultimo, errores)

    def embeddings(self, textos: Sequence[str], lote: int = 32) -> list[list[float]]:
        if not self.cfg.modelo_embeddings:
            raise LLMError("OLLAMA_EMBED_MODEL vacío: búsqueda vectorial desactivada")
        salida: list[list[float]] = []
        for i in range(0, len(textos), lote):
            parte = list(textos[i : i + lote])
            resp = self._con_reintentos(
                lambda: self._c.embeddings.create(model=self.cfg.modelo_embeddings, input=parte)
            )
            datos = sorted(resp.data, key=lambda d: d.index)
            salida.extend([list(d.embedding) for d in datos])
        return salida

    def disponible(self) -> bool:
        """True si Ollama responde y el modelo configurado existe."""
        try:
            ids = {m.id for m in self._c.models.list().data}
            return self.cfg.modelo in ids or any(i.split(":")[0] == self.cfg.modelo for i in ids)
        except Exception:  # noqa: BLE001 - cualquier fallo = no disponible
            return False

    # ───────────────────────── internos ─────────────────────────

    def _formato(self, esquema: type[BaseModel], nombre: str | None) -> dict[str, Any]:
        if self._modo_json == "object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": nombre or esquema.__name__, "schema": esquema_plano(esquema)},
        }

    def _crear_con_reintentos(self, params: dict[str, Any]) -> Any:
        try:
            return self._con_reintentos(lambda: self._c.chat.completions.create(**params))
        except Exception as e:  # noqa: BLE001
            if _es_400(e):
                msg = str(e).lower()
                # Ollama antiguo: no conoce reasoning_effort → reintento sin el campo
                if "reasoning" in msg and "extra_body" in params:
                    log.warning("Ollama rechaza reasoning_effort; se desactiva el interruptor de razonamiento")
                    self._sin_reasoning_effort = True
                    params = {k: v for k, v in params.items() if k != "extra_body"}
                    return self._crear_con_reintentos(params)
                # json_schema no soportado → json_object (la validación sigue en Pydantic)
                if params.get("response_format", {}).get("type") == "json_schema":
                    log.warning("Ollama rechaza json_schema; se usa json_object")
                    self._modo_json = "object"
                    params = dict(params, response_format={"type": "json_object"})
                    return self._crear_con_reintentos(params)
            if isinstance(e, LLMError):
                raise
            # modelo no descargado (404), credenciales, etc.: error definitivo y legible
            raise LLMError(f"{type(e).__name__}: {e}") from e

    def _con_reintentos(self, fn):
        ultimo: Exception | None = None
        for intento in range(self.cfg.reintentos + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                if not _es_transitorio(e) or intento == self.cfg.reintentos:
                    if _es_transitorio(e):
                        raise LLMError(f"Ollama no respondió tras {intento + 1} intentos: {e}") from e
                    raise
                ultimo = e
                espera = self.cfg.espera_base_s * (2**intento) * (0.75 + random.random() / 2)
                log.warning("Error transitorio (%s). Reintento %s en %.1fs", type(e).__name__, intento + 1, espera)
                time.sleep(espera)
        raise LLMError(str(ultimo))  # pragma: no cover

    def _normalizar(self, resp: Any, t0: float) -> RespuestaLLM:
        eleccion = resp.choices[0]
        m = eleccion.message
        razon = getattr(m, "reasoning", None)
        if razon is None and getattr(m, "model_extra", None):
            razon = m.model_extra.get("reasoning")
        crudo = m.content or ""
        if razon is None:
            hallado = re.search(r"<think>(.*?)</think>", crudo, re.S | re.I)
            razon = hallado.group(1).strip() if hallado else None
        llamadas = [
            LlamadaHerramienta(id=tc.id or f"call_{i}", nombre=tc.function.name, argumentos_crudos=tc.function.arguments or "{}")
            for i, tc in enumerate(getattr(m, "tool_calls", None) or [])
        ]
        asistente: dict[str, Any] = {"role": "assistant", "content": _limpiar(crudo)}
        if llamadas:
            asistente["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.nombre, "arguments": c.argumentos_crudos}}
                for c in llamadas
            ]
        uso = getattr(resp, "usage", None)
        return RespuestaLLM(
            contenido=_limpiar(crudo),
            razonamiento=razon,
            herramientas=llamadas,
            fin=eleccion.finish_reason,
            uso={
                "entrada": getattr(uso, "prompt_tokens", 0) or 0,
                "salida": getattr(uso, "completion_tokens", 0) or 0,
            },
            segundos=time.monotonic() - t0,
            modelo=getattr(resp, "model", self.cfg.modelo),
            mensaje_asistente=asistente,
        )


def esquema_plano(modelo: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema de Pydantic con los $ref resueltos en línea.

    El conversor de esquema a gramática de Ollama no garantiza soporte de
    ``$defs``/``$ref``; un esquema plano evita depender de ello.
    """
    raiz = modelo.model_json_schema()
    defs = raiz.pop("$defs", {})

    def resolver(nodo: Any) -> Any:
        if isinstance(nodo, dict):
            if "$ref" in nodo:
                return resolver(defs[nodo["$ref"].split("/")[-1]])
            return {k: resolver(v) for k, v in nodo.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(nodo, list):
            return [resolver(x) for x in nodo]
        return nodo

    return resolver(raiz)


def _codigo(e: Exception) -> int | None:
    return getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)


def _es_400(e: Exception) -> bool:
    return _codigo(e) == 400


def _es_transitorio(e: Exception) -> bool:
    nombre = type(e).__name__
    if nombre in {"APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout", "TimeoutError", "ConnectionError"}:
        return True
    c = _codigo(e)
    return c is not None and (c == 429 or c >= 500)


def _resumir_errores(e: ValidationError, maximo: int = 12) -> str:
    lineas = []
    for err in e.errors()[:maximo]:
        ruta = ".".join(str(p) for p in err.get("loc", ()))
        lineas.append(f"- {ruta or '(raíz)'}: {err.get('msg')}")
    if len(e.errors()) > maximo:
        lineas.append(f"- … y {len(e.errors()) - maximo} errores más")
    return "\n".join(lineas)
