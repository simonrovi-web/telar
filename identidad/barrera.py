"""Barrera de identidad: primero quién es la persona, después qué dice la página.

    python -m identidad.barrera        revisa docs/data/identidad.json contra el registro

Ninguna regla depende de un modelo. Un nombre solo no identifica a nadie: hace
falta una nómina de donde sale la persona y datos que la distingan de un
homónimo. Nunca se guardan RUT, domicilio ni fecha de nacimiento.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DATOS = Path(__file__).resolve().parents[1] / "docs" / "data"
PROHIBIDOS = {"rut", "run", "domicilio", "direccion", "nacimiento", "telefono", "correo", "email"}


def normalizar(s: str) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


@dataclass(frozen=True)
class Persona:
    nombres: tuple[str, ...]
    nomina: str = ""
    fuertes: tuple[str, ...] = ()
    debiles: tuple[str, ...] = ()


@dataclass
class Veredicto:
    pasa: bool
    motivo: str = ""
    anclas: list[str] = field(default_factory=list)


def de_dict(d: dict) -> Persona:
    nom = d.get("nomina") or {}
    return Persona(tuple(d.get("nombres", ())), nom.get("url", "") if isinstance(nom, dict) else str(nom),
                   tuple(d.get("fuertes", ())), tuple(d.get("debiles", ())))


def _hay(aguja: str, pajar: str) -> bool:
    a = normalizar(aguja)
    return bool(a) and re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", pajar) is not None


def admitir(p: Persona) -> Veredicto:
    """Se decide antes de buscar nada en la web."""
    if not p.nombres or min(len(normalizar(n).split()) for n in p.nombres) < 2:
        return Veredicto(False, "nombre incompleto")
    if not p.nomina.startswith(("http://", "https://")):
        return Veredicto(False, "no figura en ninguna nómina de cargos")
    if not p.fuertes and len(p.debiles) < 2:
        return Veredicto(False, "sin datos que la distingan")
    return Veredicto(True)


def _nombre_en(nombre: str, p: Persona, texto: str, norm: str) -> bool:
    """El nombre aparece, y no como parte de otro más largo.

    «Alfredo Moreno» dentro de «Alfredo Moreno Cebrián» es otra persona. El
    cuidado solo hace falta con las formas cortas: las de dos apellidos no
    admiten esa confusión.
    """
    largo = max(len(normalizar(n).split()) for n in p.nombres)
    if len(normalizar(nombre).split()) >= largo:
        return _hay(nombre, norm)
    propios = {t for n in p.nombres for t in normalizar(n).split()}
    plano = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    patron = r"(?<!\w)" + r"\s+".join(map(re.escape, normalizar(nombre).split())) + r"(?!\w)"
    for m in re.finditer(patron, plano, re.I):
        cola = re.match(r"[  ]+(?:(?:de|del|la|las|los|y|e)[  ]+){0,3}([A-Z][a-z]+)", plano[m.end():m.end() + 60])
        if not cola or normalizar(cola.group(1)) in propios:
            return True
    return False


def pagina_es_de(p: Persona, texto: str) -> Veredicto:
    norm = normalizar(texto)
    if not any(_nombre_en(n, p, texto, norm) for n in p.nombres):
        return Veredicto(False, "el nombre completo no aparece")
    fuertes = [a for a in p.fuertes if _hay(a, norm)]
    debiles = [a for a in p.debiles if _hay(a, norm)]
    if fuertes or len(debiles) >= 2:
        return Veredicto(True, anclas=fuertes + debiles)
    return Veredicto(False, "ningún dato la identifica: posible homónimo", debiles)


def revisar(ident: dict, reg: dict) -> list[str]:
    """Lo que impide publicar el registro."""
    out = []
    ps = ident.get("personas", {})
    for k, p in reg["personas"].items():
        if k not in ps:
            out.append(f"{k}: sin identidad")
            continue
        v = admitir(de_dict(ps[k]))
        if not v.pasa:
            out.append(f"{k}: {v.motivo}")
        if normalizar(p["nombre"]) not in {normalizar(n) for n in ps[k].get("nombres", [])}:
            out.append(f"{k}: el nombre de la ficha no está entre sus formas")
        for i in ps[k].get("ids", []):
            if i.get("estado") not in ("corroborado", "sin corroborar") or not str(i.get("url", "")).startswith("https://"):
                out.append(f"{k}: identificador mal declarado")
            if i.get("estado") == "corroborado" and not i.get("por"):
                out.append(f"{k}: dice corroborado sin decir con qué")
    for k in ps:
        if k not in reg["personas"]:
            out.append(f"{k}: identidad sin ficha")
    vistos: dict[str, str] = {}
    for k, d in ps.items():
        for n in d.get("nombres", []):
            otro = vistos.setdefault(normalizar(n), k)
            if otro != k:
                out.append(f"{k} y {otro} comparten la forma «{n}»")
        for i in d.get("ids", []):
            c = f"{i.get('esquema')}:{i.get('id')}"
            otro = vistos.setdefault(c, k)
            if otro != k:
                out.append(f"{k} y {otro} comparten el identificador {c}")

    def campos(x):
        if isinstance(x, dict):
            for a, b in x.items():
                yield a
                yield from campos(b)
        elif isinstance(x, list):
            for b in x:
                yield from campos(b)
    malos = {c for c in campos(ident) if normalizar(c) in PROHIBIDOS}
    if malos:
        out.append(f"campos personales que no se guardan: {sorted(malos)}")
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ident = json.loads((DATOS / "identidad.json").read_text(encoding="utf-8"))
    reg = json.loads((DATOS / "registro.json").read_text(encoding="utf-8"))
    malos = revisar(ident, reg)
    for m in malos:
        print("MAL ", m)
    print(f"{len(reg['personas'])} fichas, {len(malos)} problemas de identidad")
    return 1 if malos else 0


if __name__ == "__main__":
    raise SystemExit(main())
