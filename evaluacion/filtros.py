"""Filtros deterministas de admisión. Ninguno depende de un modelo.

El orden es el de la regla de la casa: primero quién es la persona, después si
la página habla de ella, y solo al final qué dice la página.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from telar_motor.entity_extractor import cita_literal
from telar_motor.ontologia import normalizar

TIPOS = ("cargo", "empresa", "dinero", "afiliacion", "personal")
# Nunca se publican solos: van a revisión humana aunque pasen todo lo demás.
SOLO_HUMANO = {"dinero", "personal"}
CITA_MINIMA = 20

# Lenguaje que no confirma. Se busca en la cita y en la frase que la rodea.
_DUDA = re.compile(
    r"\b(no confirmad[oa]s?|sin confirmar|trascendi[oó]|habr[ií]an?|se rumorea|rumores?|presunt[oa]s?|"
    r"presuntamente|supuest[oa]s?|supuestamente|al parecer|aparentemente|se especula|especulaciones|"
    r"versiones no oficiales|fuentes? an[oó]nimas?|podr[ií]an? haber)\b", re.I)


@dataclass(frozen=True)
class Persona:
    """Alguien de una nómina. Sin nómina ni anclas no se investiga a nadie."""
    nombres: tuple[str, ...]
    nomina: str = ""
    anclas_fuertes: tuple[str, ...] = ()
    anclas_debiles: tuple[str, ...] = ()


@dataclass
class Veredicto:
    pasa: bool
    motivo: str = ""
    anclas: list[str] = field(default_factory=list)


def _hay(aguja: str, pajar: str) -> bool:
    """Coincidencia por palabra completa sobre texto normalizado."""
    a = normalizar(aguja)
    return bool(a) and re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", pajar) is not None


def admitir_encargo(p: Persona) -> Veredicto:
    if not p.nombres or min(len(normalizar(n).split()) for n in p.nombres) < 2:
        return Veredicto(False, "nombre incompleto")
    if not p.nomina:
        return Veredicto(False, "no figura en ninguna nómina de cargos")
    if not p.anclas_fuertes and len(p.anclas_debiles) < 2:
        return Veredicto(False, "sin anclas de identidad")
    return Veredicto(True)


def pagina_es_de(p: Persona, texto: str) -> Veredicto:
    """La página debe traer el nombre completo y probar que es esa persona."""
    t = normalizar(texto)
    if not any(_hay(n, t) for n in p.nombres):
        return Veredicto(False, "el nombre completo no aparece")
    fuertes = [a for a in p.anclas_fuertes if _hay(a, t)]
    debiles = [a for a in p.anclas_debiles if _hay(a, t)]
    if fuertes or len(debiles) >= 2:
        return Veredicto(True, anclas=fuertes + debiles)
    return Veredicto(False, "ningún dato la identifica: posible homónimo", anclas=debiles)


def frase_de(cita: str, texto: str, margen: int = 250) -> str:
    """La cita con su entorno, para leer la duda que el recorte pudo dejar fuera."""
    limpio = re.sub(r"\s+", " ", texto)
    c = re.sub(r"\s+", " ", cita).strip()
    i = limpio.lower().find(c.lower())
    if i < 0:
        return c
    ini = max((limpio.rfind(s, 0, i) for s in (". ", "! ", "? ")), default=-1)
    ini = max(ini + 1, i - margen)
    fin = re.search(r"[.!?](?:\s|$)", limpio[i + len(c):])
    f = i + len(c) + (fin.end() if fin and not c.endswith((".", "!", "?")) else 0)
    return limpio[ini:f].strip()


def revisar_vinculo(v: dict, texto: str, p: Persona) -> Veredicto:
    tipo = str(v.get("tipo") or "").strip().lower()
    contraparte = str(v.get("contraparte") or "").strip()
    clase = str(v.get("contraparte_tipo") or "").strip().lower()
    cita = str(v.get("cita") or "").strip()
    if tipo not in TIPOS:
        return Veredicto(False, "tipo de vínculo desconocido")
    if len(normalizar(contraparte)) < 2:
        return Veredicto(False, "sin contraparte")
    if any(set(normalizar(contraparte).split()) <= set(normalizar(n).split()) for n in p.nombres):
        return Veredicto(False, "vínculo de la persona consigo misma")
    if clase not in ("persona", "organizacion"):
        return Veredicto(False, "la contraparte no es persona ni organización")
    if clase == "persona" and tipo in ("cargo", "empresa", "afiliacion"):
        return Veredicto(False, "una persona no puede ser la organización del vínculo")
    if len(cita) < CITA_MINIMA or not cita_literal(cita, texto):
        return Veredicto(False, "la cita no es literal")
    if not _hay(contraparte, normalizar(cita)):
        return Veredicto(False, "la contraparte no está en la cita")
    if _DUDA.search(frase_de(cita, texto)):
        return Veredicto(False, "la fuente no lo da por confirmado")
    for campo in ("desde", "hasta"):
        a = str(v.get(campo) or "").strip()
        if a and (not re.fullmatch(r"(1[89]|20)\d{2}", a) or a not in cita):
            return Veredicto(False, "el año no está en la cita")
    return Veredicto(True, "solo con revisión humana" if tipo in SOLO_HUMANO else "")


def clave(v: dict) -> tuple:
    """Identidad estable de una afirmación: no cambia entre corridas."""
    return (normalizar(v.get("tipo", "")), normalizar(v.get("contraparte", "")),
            normalizar(v.get("etiqueta", "")), str(v.get("desde") or ""))
