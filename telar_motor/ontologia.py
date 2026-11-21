"""Ontología del motor: tipos de entidad, tipos de relación y modelos Pydantic.

Los tipos de relación son un vocabulario cerrado. Un modelo pequeño inventa
etiquetas libres ("trabaja junto a", "es cercano de") que luego no se pueden
consultar ni exportar; el enum lo impide y Pydantic rechaza lo que quede fuera.
"""
from __future__ import annotations

import re
import unicodedata
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TipoEntidad(str, Enum):
    persona = "persona"
    organizacion = "organizacion"
    ubicacion = "ubicacion"
    fecha = "fecha"
    documento = "documento"
    monto_contrato = "monto_contrato"


class TipoRelacion(str, Enum):
    # cargos y trayectoria
    ocupa_cargo_en = "ocupa_cargo_en"            # persona → organización
    representa_a = "representa_a"                # persona → organización (representante legal)
    # empresas y propiedad
    propietario_de = "propietario_de"            # persona|org → organización
    socio_de = "socio_de"                        # persona → organización
    filial_de = "filial_de"                      # organización → organización
    # dinero y política
    adjudica_a = "adjudica_a"                    # organización → organización|persona
    contrata_con = "contrata_con"                # organización → organización|persona
    paga_a = "paga_a"                            # persona|org → persona|org
    dona_a = "dona_a"                            # persona|org → persona|org
    tiene_monto = "tiene_monto"                  # documento|contrato → monto_contrato
    # afiliaciones
    miembro_de = "miembro_de"                    # persona → organización
    # vínculos personales (solo si el texto los documenta)
    familiar_de = "familiar_de"                  # persona → persona
    asociado_a = "asociado_a"                    # persona → persona (relación profesional documentada)
    # documentos, lugares y fechas
    firma = "firma"                              # persona → documento
    emite = "emite"                              # organización → documento
    menciona = "menciona"                        # documento → cualquier entidad
    ubicado_en = "ubicado_en"                    # cualquier entidad → ubicación
    ocurre_en_fecha = "ocurre_en_fecha"          # documento|contrato → fecha


# Correspondencia con las dimensiones de Telar (cargo, empresa, dinero,
# afiliacion, personal). Las relaciones sin dimensión no se exportan como vínculo.
DIMENSION_TELAR: dict[TipoRelacion, str] = {
    TipoRelacion.ocupa_cargo_en: "cargo",
    TipoRelacion.representa_a: "empresa",
    TipoRelacion.propietario_de: "empresa",
    TipoRelacion.socio_de: "empresa",
    TipoRelacion.adjudica_a: "dinero",
    TipoRelacion.contrata_con: "dinero",
    TipoRelacion.paga_a: "dinero",
    TipoRelacion.dona_a: "dinero",
    TipoRelacion.miembro_de: "afiliacion",
    TipoRelacion.familiar_de: "personal",
    TipoRelacion.asociado_a: "personal",
}

DESCRIPCION_ONTOLOGIA = """\
TIPOS DE ENTIDAD
- persona: ser humano nombrado en el texto.
- organizacion: empresa, organismo público, municipio, partido, fundación, tribunal, medio.
- ubicacion: ciudad, comuna, región, país, dirección.
- fecha: fecha concreta o período ("14 de marzo de 2024", "2019").
- documento: decreto, contrato, resolución, licitación, oficio, informe, sentencia (con su número si existe).
- monto_contrato: cifra de dinero asociada a un contrato o pago, con moneda ("$ 1.240 millones").

TIPOS DE RELACIÓN (source → target)
- ocupa_cargo_en: persona → organización. attributes de la persona pueden llevar "cargo".
- representa_a: persona → organización (representante legal o apoderado).
- propietario_de / socio_de: persona u organización → organización.
- filial_de: organización → organización.
- adjudica_a / contrata_con: organización → organización o persona.
- paga_a / dona_a: persona u organización → persona u organización.
- tiene_monto: documento → monto_contrato.
- miembro_de: persona → organización (partido, gremio, directorio).
- familiar_de: persona → persona, SOLO si el texto declara el parentesco.
- asociado_a: persona → persona, SOLO si el texto documenta una relación profesional.
- firma: persona → documento. emite: organización → documento.
- menciona: documento → entidad.
- ubicado_en: entidad → ubicación.
- ocurre_en_fecha: documento → fecha.
"""


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, espacios colapsados: clave de comparación."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def slug(texto: str, maximo: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalizar(texto)).strip("-")[:maximo].strip("-")


class Entidad(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    id: str = Field(description="Identificador corto y único dentro de esta respuesta, p. ej. e1, e2.")
    name: str = Field(min_length=1, description="Nombre tal como aparece en el texto.")
    type: TipoEntidad
    aliases: list[str] = Field(default_factory=list, description="Otras formas en que el texto nombra a la entidad.")
    attributes: dict[str, str] = Field(
        default_factory=dict, description="Atributos explícitos en el texto: cargo, rut, número, moneda, rol…"
    )

    @field_validator("name")
    @classmethod
    def _sin_espacios(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    @field_validator("aliases")
    @classmethod
    def _alias_unicos(cls, v: list[str]) -> list[str]:
        vistos, salida = set(), []
        for a in v:
            a = re.sub(r"\s+", " ", a or "").strip()
            if a and normalizar(a) not in vistos:
                vistos.add(normalizar(a))
                salida.append(a)
        return salida

    @field_validator("attributes", mode="before")
    @classmethod
    def _atributos_texto(cls, v):
        # los modelos a veces devuelven números o listas: se aplanan a texto
        if not isinstance(v, dict):
            return {}
        return {str(k): (", ".join(map(str, x)) if isinstance(x, list) else str(x)) for k, x in v.items() if x is not None}


class Relacion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = Field(description="id de la entidad origen.")
    relation_type: TipoRelacion
    target: str = Field(description="id de la entidad destino.")
    evidence_snippet: str = Field(
        min_length=8, description="Cita LITERAL y breve del texto que sostiene la relación (copiada sin cambios)."
    )
    confidence: float = Field(ge=0.0, le=1.0)


class Extraccion(BaseModel):
    """Esquema que el modelo debe devolver."""

    model_config = ConfigDict(extra="ignore")

    entities: list[Entidad] = Field(default_factory=list)
    relations: list[Relacion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherencia(self) -> "Extraccion":
        ids = [e.id for e in self.entities]
        repetidos = {i for i in ids if ids.count(i) > 1}
        if repetidos:
            raise ValueError(f"ids de entidad repetidos: {sorted(repetidos)}")
        conocidos = set(ids)
        huerfanas = [
            f"{r.source}->{r.target}" for r in self.relations if r.source not in conocidos or r.target not in conocidos
        ]
        if huerfanas:
            raise ValueError(f"relaciones que apuntan a ids inexistentes: {huerfanas[:8]}")
        return self
