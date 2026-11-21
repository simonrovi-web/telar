"""Extracción de entidades y relaciones en español con verificación posterior.

El modelo propone; el código verifica. Tres filtros deterministas descartan lo
que el texto no sostiene, con la misma lógica con que se comprueban las
cifras de un resumen contra sus datos:

1. Cada entidad debe aparecer en el texto (su nombre o uno de sus alias).
2. Cada ``evidence_snippet`` debe ser una cita literal del texto (se toleran
   diferencias de espacios, comillas y guiones, nada más).
3. Cada relación debe respetar el dominio y rango de su tipo (una persona no
   "emite" un decreto; una organización no es "familiar_de" nadie).

Lo descartado no se pierde: queda en ``descartes`` con el motivo, para revisión.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import CFG, MotorConfig
from .llm_client import LLMClient, LLMError
from .ontologia import (
    DESCRIPCION_ONTOLOGIA,
    Entidad,
    Extraccion,
    TipoEntidad as TE,
    TipoRelacion as TR,
    normalizar,
    slug,
)

log = logging.getLogger("telar_motor.extractor")

_ACTOR = {TE.persona, TE.organizacion}
_TODOS = set(TE)
DOMINIO_RANGO: dict[TR, tuple[set[TE], set[TE]]] = {
    TR.ocupa_cargo_en: ({TE.persona}, {TE.organizacion}),
    TR.representa_a: ({TE.persona}, {TE.organizacion}),
    TR.propietario_de: (_ACTOR, {TE.organizacion}),
    TR.socio_de: (_ACTOR, {TE.organizacion}),
    TR.filial_de: ({TE.organizacion}, {TE.organizacion}),
    TR.adjudica_a: ({TE.organizacion}, _ACTOR),
    TR.contrata_con: ({TE.organizacion}, _ACTOR),
    TR.paga_a: (_ACTOR, _ACTOR),
    TR.dona_a: (_ACTOR, _ACTOR),
    TR.tiene_monto: ({TE.documento}, {TE.monto_contrato}),
    TR.miembro_de: ({TE.persona}, {TE.organizacion}),
    TR.familiar_de: ({TE.persona}, {TE.persona}),
    TR.asociado_a: ({TE.persona}, {TE.persona}),
    TR.firma: ({TE.persona}, {TE.documento}),
    TR.emite: ({TE.organizacion}, {TE.documento}),
    TR.menciona: ({TE.documento}, _TODOS),
    TR.ubicado_en: (_TODOS - {TE.ubicacion, TE.fecha}, {TE.ubicacion}),
    TR.ocurre_en_fecha: ({TE.documento, TE.monto_contrato}, {TE.fecha}),
}

SISTEMA = f"""\
Eres un analista de investigación periodística. Extraes entidades y relaciones
de documentos en español y devuelves SOLO un objeto JSON.

REGLAS (obligatorias):
1. Extrae únicamente lo que el texto dice de forma explícita. No completes con
   conocimiento propio, no deduzcas parentescos, cargos ni montos.
2. "name" se copia tal como aparece en el texto. Usa "aliases" para otras
   formas del mismo nombre presentes en el texto (siglas, apellidos solos).
3. "evidence_snippet" es una cita LITERAL, copiada carácter por carácter del
   texto, de entre 8 y 300 caracteres, que por sí sola sostiene la relación.
4. "confidence" entre 0 y 1: 0.9 o más solo si la cita es inequívoca.
5. Usa solo los tipos de entidad y de relación listados. Si una relación no
   encaja en ninguno, no la incluyas.
6. Los ids son cortos y únicos (e1, e2, …) y toda relación apunta a ids que
   existan en "entities".
7. Si el texto no contiene entidades, devuelve {{"entities": [], "relations": []}}.

{DESCRIPCION_ONTOLOGIA}
FORMATO
{{"entities":[{{"id":"e1","name":"…","type":"persona","aliases":[],"attributes":{{"cargo":"…"}}}}],
 "relations":[{{"source":"e1","relation_type":"ocupa_cargo_en","target":"e2","evidence_snippet":"…","confidence":0.9}}]}}
"""

# las comillas son tipografía: se eliminan para que su posición no invalide una cita
_COMILLAS = str.maketrans({"«": "", "»": "", "“": "", "”": "", "„": "", '"': "", "‘": "'", "’": "'",
                           "–": "-", "—": "-", "\u00a0": " "})


def _canon(texto: str) -> str:
    """Normalización para comparar citas: sin comillas, guiones unificados, espacios, mayúsculas."""
    return re.sub(r"\s+", " ", (texto or "").translate(_COMILLAS)).strip().lower()


def cita_literal(cita: str, texto: str) -> bool:
    """True si ``cita`` aparece en ``texto``. Admite "…"/"..." como corte entre tramos literales."""
    c, t = _canon(cita), _canon(texto)
    if not c:
        return False
    if c in t:
        return True
    tramos = [x.strip(" .") for x in re.split(r"\s*(?:…|\.\.\.)\s*", c) if x.strip(" .")]
    if len(tramos) < 2:
        return False
    pos = 0
    for tr in tramos:
        if len(tr) < 4:
            return False
        i = t.find(tr, pos)
        if i < 0:
            return False
        pos = i + len(tr)
    return True


def aparece(entidad: Entidad, texto_norm: str) -> bool:
    candidatos = [entidad.name, *entidad.aliases]
    return any(normalizar(c) and normalizar(c) in texto_norm for c in candidatos)


def fragmentar(texto: str, maximo: int) -> list[str]:
    """Corta por párrafos (y por oraciones si un párrafo es muy largo)."""
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    piezas: list[str] = []
    for p in parrafos:
        if len(p) <= maximo:
            piezas.append(p)
            continue
        actual = ""
        for o in re.split(r"(?<=[.;:!?])\s+", p):
            if actual and len(actual) + len(o) + 1 > maximo:
                piezas.append(actual)
                actual = o
            else:
                actual = f"{actual} {o}".strip()
        if actual:
            piezas.append(actual)
    bloques, actual = [], ""
    for pz in piezas:
        if actual and len(actual) + len(pz) + 2 > maximo:
            bloques.append(actual)
            actual = pz
        else:
            actual = f"{actual}\n\n{pz}".strip()
    if actual:
        bloques.append(actual)
    return bloques or [texto]


@dataclass
class EntidadGlobal:
    id: str                      # "persona:rodrigo-valdes-iturra"
    nombre: str
    tipo: str
    alias: list[str] = field(default_factory=list)
    atributos: dict[str, str] = field(default_factory=dict)


@dataclass
class RelacionGlobal:
    origen: str
    tipo: str
    destino: str
    evidencia: str
    confianza: float
    doc_id: str | None = None


@dataclass
class ResultadoExtraccion:
    doc_id: str | None
    entidades: list[EntidadGlobal]
    relaciones: list[RelacionGlobal]
    descartes: list[dict[str, Any]]
    errores: list[str]
    segundos: float
    fragmentos: int
    modelo: str

    def resumen(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "entidades": len(self.entidades),
            "relaciones": len(self.relaciones),
            "descartes": len(self.descartes),
            "errores": len(self.errores),
            "fragmentos": self.fragmentos,
            "segundos": round(self.segundos, 2),
            "modelo": self.modelo,
        }


class EntityExtractor:
    def __init__(self, llm: LLMClient, cfg: MotorConfig = CFG):
        self.llm = llm
        self.cfg = cfg

    def extraer(self, texto: str, doc_id: str | None = None, titulo: str = "") -> ResultadoExtraccion:
        t0 = time.monotonic()
        indice: dict[str, EntidadGlobal] = {}   # id global → entidad
        claves: dict[tuple[str, str], str] = {}  # (tipo, nombre/alias normalizado) → id global
        relaciones: dict[tuple[str, str, str], RelacionGlobal] = {}
        descartes: list[dict[str, Any]] = []
        errores: list[str] = []
        bloques = fragmentar(texto, self.cfg.fragmento_chars)

        for n, bloque in enumerate(bloques, 1):
            cabecera = f"DOCUMENTO: {titulo}\n" if titulo else ""
            mensajes = [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": f"{cabecera}FRAGMENTO {n}/{len(bloques)}\nTEXTO:\n<<<\n{bloque}\n>>>"},
            ]
            try:
                ext, _ = self.llm.estructurado(
                    mensajes, Extraccion, modo="extraccion", razonar=self.cfg.razonar_extraccion, nombre="extraccion"
                )
            except LLMError as e:
                errores.append(f"fragmento {n}: {e}")
                log.warning("Fragmento %s sin extracción: %s", n, e)
                continue
            self._integrar(ext, bloque, indice, claves, relaciones, descartes, doc_id)

        return ResultadoExtraccion(
            doc_id=doc_id,
            entidades=list(indice.values()),
            relaciones=list(relaciones.values()),
            descartes=descartes,
            errores=errores,
            segundos=time.monotonic() - t0,
            fragmentos=len(bloques),
            modelo=self.cfg.modelo,
        )

    # ───────────────────── verificación e integración ─────────────────────

    def _integrar(self, ext: Extraccion, bloque: str, indice, claves, relaciones, descartes, doc_id) -> None:
        texto_norm = normalizar(bloque)
        local: dict[str, EntidadGlobal] = {}

        for e in ext.entities:
            if not aparece(e, texto_norm):
                descartes.append({"tipo": "entidad", "valor": e.name, "motivo": "no aparece en el texto"})
                continue
            local[e.id] = self._resolver(e, indice, claves)

        for r in ext.relations:
            a, b = local.get(r.source), local.get(r.target)
            etiqueta = f"{r.source} {r.relation_type.value} {r.target}"
            if not a or not b:
                descartes.append({"tipo": "relacion", "valor": etiqueta, "motivo": "entidad descartada"})
                continue
            if a.id == b.id:
                descartes.append({"tipo": "relacion", "valor": etiqueta, "motivo": "relación reflexiva"})
                continue
            dom, rng = DOMINIO_RANGO[r.relation_type]
            if TE(a.tipo) not in dom or TE(b.tipo) not in rng:
                descartes.append(
                    {"tipo": "relacion", "valor": f"{a.nombre} {r.relation_type.value} {b.nombre}",
                     "motivo": f"tipos incompatibles ({a.tipo} → {b.tipo})"}
                )
                continue
            if not cita_literal(r.evidence_snippet, bloque):
                descartes.append(
                    {"tipo": "relacion", "valor": f"{a.nombre} {r.relation_type.value} {b.nombre}",
                     "motivo": "la evidencia no es una cita literal", "evidencia": r.evidence_snippet[:200]}
                )
                continue
            if r.confidence < self.cfg.confianza_minima:
                descartes.append(
                    {"tipo": "relacion", "valor": f"{a.nombre} {r.relation_type.value} {b.nombre}",
                     "motivo": f"confianza {r.confidence:.2f} bajo el mínimo"}
                )
                continue
            clave = (a.id, r.relation_type.value, b.id)
            previa = relaciones.get(clave)
            if previa is None or r.confidence > previa.confianza:
                relaciones[clave] = RelacionGlobal(
                    a.id, r.relation_type.value, b.id, r.evidence_snippet.strip(), round(r.confidence, 3), doc_id
                )

    @staticmethod
    def _resolver(e: Entidad, indice: dict, claves: dict) -> EntidadGlobal:
        """Fusiona entidades del mismo tipo que comparten nombre o alias."""
        tipo = e.type.value
        nombres = [e.name, *e.aliases]
        for n in nombres:
            gid = claves.get((tipo, normalizar(n)))
            if gid:
                g = indice[gid]
                for x in nombres:
                    if normalizar(x) != normalizar(g.nombre) and normalizar(x) not in map(normalizar, g.alias):
                        g.alias.append(x)
                    claves[(tipo, normalizar(x))] = gid
                for k, v in e.attributes.items():
                    g.atributos.setdefault(k, v)
                return g
        gid = f"{tipo}:{slug(e.name)}"
        base, k = gid, 2
        while gid in indice:  # mismo slug, distinto nombre normalizado
            gid, k = f"{base}-{k}", k + 1
        g = EntidadGlobal(gid, e.name, tipo, list(e.aliases), dict(e.attributes))
        indice[gid] = g
        for n in nombres:
            claves[(tipo, normalizar(n))] = gid
        return g
