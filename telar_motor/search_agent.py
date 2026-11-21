"""Agente de búsqueda con tool calling y citas verificadas.

Bucle: el modelo decide qué herramienta usar, el código la ejecuta y devuelve
resultados con referencias F#, y así hasta que el modelo responde sin pedir
herramientas (o se agota ``max_pasos``). Ollama no admite ``tool_choice``, así
que la obligación de buscar antes de responder se impone con el prompt y con
un recordatorio si el modelo intenta responder sin evidencia.

Verificación posterior, sin modelo:
* toda referencia citada debe existir en el registro de evidencia;
* toda cita textual entre «» o comillas debe aparecer literalmente en la
  evidencia citada en la misma oración (o, en su defecto, en cualquier evidencia);
* se cuentan las oraciones con afirmaciones que no llevan referencia.
La sección de fuentes la arma el código desde el registro, no el modelo.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from .almacen import Almacen
from .config import CFG, MotorConfig
from .entity_extractor import cita_literal
from .herramientas import ESPECIFICACION, Evidencia, Herramientas
from .llm_client import LLMClient, LLMError

log = logging.getLogger("telar_motor.agente")

SISTEMA = """\
Eres un investigador periodístico. Respondes en español preguntas sobre un
corpus de documentos usando SOLO lo que devuelven tus herramientas.

PROCEDIMIENTO
1. Antes de responder, usa al menos una herramienta. Combina:
   - search_documents para encontrar texto relevante;
   - get_entity_connections para ver vínculos documentados de una persona u organización;
   - query_structured_records para montos, fechas y proveedores de contratos.
2. Cuando tengas evidencia suficiente, responde sin pedir más herramientas.

REGLAS DE LA RESPUESTA
- Cada afirmación lleva al final su referencia entre corchetes: [F1] o [F2, F5].
  Usa solo referencias que te entregaron las herramientas.
- Si citas palabras de un documento, ponlas entre «» y cópialas exactamente.
- No agregues datos que no estén en la evidencia. No deduzcas intenciones ni
  delitos: describe lo que los documentos registran.
- Si la evidencia no alcanza para responder, dilo explícitamente y explica qué faltaría.
- No escribas una sección de fuentes: el sistema la agrega.
"""

RECORDATORIO = (
    "Todavía no has consultado ninguna herramienta. Usa search_documents, get_entity_connections o "
    "query_structured_records antes de responder."
)
CIERRE = (
    "Se agotó el número de pasos. Responde ahora con la evidencia reunida, citando [F#], "
    "y señala qué quedó sin verificar."
)

_REF = re.compile(r"\[(F\d+(?:\s*[,;]\s*F\d+)*)\]")
_CITA = re.compile(r"«([^»]{12,})»|\"([^\"]{12,})\"|“([^”]{12,})”")
# corte de oración que no rompe cifras como "$ 1.240 millones" ni separa un [F#] final
_CORTE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡«\"“(])|\n+")
_SIN_EVIDENCIA = re.compile(r"no (hay|se encontr|encontr|consta|existe|dispongo|tengo)|sin evidencia|no es posible", re.I)


@dataclass
class Paso:
    n: int
    herramienta: str
    argumentos: dict[str, Any]
    referencias: list[str]
    error: str | None
    segundos_modelo: float


@dataclass
class RespuestaAgente:
    pregunta: str
    respuesta: str
    citas: list[Evidencia]
    pasos: list[Paso]
    verificacion: dict[str, Any]
    advertencias: list[str]
    segundos: float
    modelo: str
    razonamiento: str | None = None
    evidencia_total: int = 0

    @property
    def fuentes(self) -> str:
        lineas = []
        for ev in self.citas:
            partes = [f"[{ev.ref}]", ev.titulo or ev.doc_id or "Registro"]
            if ev.fecha:
                partes.append(ev.fecha)
            if ev.url:
                partes.append(ev.url)
            lineas.append(" · ".join(partes) + f"\n      «{_recorte(ev.texto, 220)}»")
        return "\n".join(lineas)

    def texto_completo(self) -> str:
        return f"{self.respuesta}\n\nFuentes\n{self.fuentes}" if self.citas else self.respuesta

    def dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fuentes"] = self.fuentes
        return d


class SearchAgent:
    def __init__(self, llm: LLMClient, almacen: Almacen, cfg: MotorConfig = CFG):
        self.llm = llm
        self.almacen = almacen
        self.cfg = cfg

    def preguntar(self, pregunta: str) -> RespuestaAgente:
        t0 = time.monotonic()
        h = Herramientas(self.almacen, k=self.cfg.resultados_por_busqueda)
        mensajes: list[dict[str, Any]] = [
            {"role": "system", "content": SISTEMA},
            {"role": "user", "content": pregunta},
        ]
        pasos: list[Paso] = []
        advertencias: list[str] = []
        recordado = False
        final, razonamiento = "", None

        for n in range(1, self.cfg.max_pasos_agente + 1):
            try:
                r = self.llm.chat(mensajes, modo="sintesis", razonar=self.cfg.razonar_agente, herramientas=ESPECIFICACION)
            except LLMError as e:
                advertencias.append(f"El motor falló en el paso {n}: {e}")
                break
            razonamiento = r.razonamiento or razonamiento
            if r.herramientas:
                mensajes.append(r.mensaje_asistente)
                for llamada in r.herramientas:
                    args = llamada.argumentos()
                    antes = set(h.evidencia.items)
                    salida = h.ejecutar(llamada.nombre, args)
                    error = json.loads(salida).get("error")
                    pasos.append(Paso(n, llamada.nombre, args, sorted(set(h.evidencia.items) - antes, key=_orden), error, round(r.segundos, 2)))
                    mensajes.append({"role": "tool", "tool_call_id": llamada.id, "name": llamada.nombre, "content": salida})
                continue
            if not h.evidencia.items and not recordado:
                recordado = True
                mensajes.append({"role": "assistant", "content": r.contenido})
                mensajes.append({"role": "user", "content": RECORDATORIO})
                continue
            final = r.contenido
            break
        else:
            advertencias.append(f"Se alcanzó el máximo de {self.cfg.max_pasos_agente} pasos")
            try:
                r = self.llm.chat(mensajes + [{"role": "user", "content": CIERRE}], modo="sintesis")
                final = r.contenido
            except LLMError as e:
                advertencias.append(f"El motor falló al cerrar: {e}")

        if not final:
            final = "No se pudo generar una respuesta verificable con la evidencia disponible."
        if not h.evidencia.items:
            advertencias.append("La respuesta no se apoya en ninguna herramienta: trátala como no verificada")

        texto, citas, verif = verificar_respuesta(final, h.evidencia.items)
        advertencias.extend(verif.pop("advertencias"))
        return RespuestaAgente(
            pregunta=pregunta, respuesta=texto, citas=citas, pasos=pasos, verificacion=verif,
            advertencias=advertencias, segundos=round(time.monotonic() - t0, 2), modelo=self.cfg.modelo,
            razonamiento=razonamiento, evidencia_total=len(h.evidencia.items),
        )


def verificar_respuesta(texto: str, registro: dict[str, Evidencia]) -> tuple[str, list[Evidencia], dict[str, Any]]:
    advertencias: list[str] = []
    inventadas: set[str] = set()

    def _limpiar_refs(m: re.Match) -> str:
        refs = [x.strip() for x in re.split(r"[,;]", m.group(1))]
        validas = [x for x in refs if x in registro]
        inventadas.update(x for x in refs if x not in registro)
        return f"[{', '.join(validas)}]" if validas else ""

    texto = _REF.sub(_limpiar_refs, texto).strip()
    if inventadas:
        advertencias.append(f"Se eliminaron referencias inexistentes: {', '.join(sorted(inventadas, key=_orden))}")

    usadas: list[str] = []
    for m in _REF.finditer(texto):
        for x in re.split(r"[,;]", m.group(1)):
            x = x.strip()
            if x not in usadas:
                usadas.append(x)

    citas_ok, citas_mal = 0, []
    sin_ref = 0
    for oracion in (x.strip() for x in _CORTE.split(texto)):
        if len(oracion) < 25:
            continue
        refs = [x.strip() for m in _REF.finditer(oracion) for x in re.split(r"[,;]", m.group(1))]
        if not refs and not _SIN_EVIDENCIA.search(oracion):
            sin_ref += 1
        for c in _CITA.finditer(oracion):
            literal = next(g for g in c.groups() if g)
            fuentes = [registro[x].texto for x in refs if x in registro] or [e.texto for e in registro.values()]
            if any(cita_literal(literal, f) for f in fuentes):
                citas_ok += 1
            else:
                citas_mal.append(literal[:120])
    if citas_mal:
        advertencias.append(f"{len(citas_mal)} cita(s) textual(es) no aparecen en la evidencia citada")
    if sin_ref:
        advertencias.append(f"{sin_ref} oración(es) con afirmaciones sin referencia")

    return texto, [registro[x] for x in usadas], {
        "referencias_usadas": usadas,
        "referencias_inventadas": sorted(inventadas, key=_orden),
        "citas_textuales_verificadas": citas_ok,
        "citas_textuales_no_verificadas": citas_mal,
        "oraciones_sin_referencia": sin_ref,
        "verificada": not inventadas and not citas_mal and sin_ref == 0 and bool(usadas),
        "advertencias": advertencias,
    }


def _orden(ref: str) -> int:
    return int(ref[1:]) if ref[1:].isdigit() else 10**9


def _recorte(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"
