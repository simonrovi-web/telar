"""Herramientas que el agente puede invocar (tool calling) y su registro de evidencia.

Cada resultado que vuelve al modelo lleva una referencia corta (F1, F2, …) que
apunta a una fuente exacta: fragmento de documento, relación con su cita o fila
de registro. El agente solo puede citar referencias que una herramienta le
entregó; el verificador del agente lo comprueba.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from .almacen import Almacen, ConsultaRechazada


# ───────────────────────── argumentos validados ─────────────────────────

class FiltrosBusqueda(BaseModel):
    tipo: str | None = Field(None, description="Tipo de documento: contrato, decreto, prensa, declaracion…")
    fuente: str | None = Field(None, description="Parte del nombre de la fuente (p. ej. 'Contraloría').")
    fecha_desde: str | None = Field(None, description="AAAA-MM-DD")
    fecha_hasta: str | None = Field(None, description="AAAA-MM-DD")


class ArgsBuscar(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    filters: FiltrosBusqueda | None = None


class ArgsConexiones(BaseModel):
    entity_name: str = Field(min_length=2, max_length=200)
    depth: int = Field(1, ge=1, le=3)


class CondicionFiltro(BaseModel):
    campo: str
    op: Literal["=", "!=", ">", ">=", "<", "<=", "contiene", "entre", "en"] = "="
    valor: Any


class FiltroRegistros(BaseModel):
    tabla: Literal["registros", "entidades", "relaciones", "documentos"] = "registros"
    campos: list[str] | None = None
    donde: list[CondicionFiltro] = Field(default_factory=list)
    orden: str | None = None
    limite: int = Field(20, ge=1, le=100)


class ArgsRegistros(BaseModel):
    sql_or_filter: FiltroRegistros | str

    @field_validator("sql_or_filter", mode="before")
    @classmethod
    def _json_en_texto(cls, v):
        # algunos modelos envían el filtro como cadena JSON
        if isinstance(v, str) and v.strip().startswith("{"):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v


# ───────────────────────── especificación para el modelo ─────────────────────────

ESPECIFICACION: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Busca en el corpus de documentos (contratos, decretos, prensa, declaraciones) por texto y "
                "significado. Devuelve fragmentos con su referencia F#, título, fecha y URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Qué buscar, en español."},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "tipo": {"type": "string"},
                            "fuente": {"type": "string"},
                            "fecha_desde": {"type": "string", "description": "AAAA-MM-DD"},
                            "fecha_hasta": {"type": "string", "description": "AAAA-MM-DD"},
                        },
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_connections",
            "description": (
                "Devuelve las relaciones documentadas de una persona u organización en el grafo, hasta la "
                "profundidad indicada (1 a 3), cada una con su cita textual y referencia F#."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Nombre o alias de la entidad."},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["entity_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_structured_records",
            "description": (
                "Consulta registros estructurados (contratos con monto, organismo, proveedor, fecha). Acepta un "
                "filtro JSON {tabla, campos, donde:[{campo, op, valor}], orden, limite} o un único SELECT de solo "
                "lectura. Tablas: registros(id,tipo,fecha,organismo,proveedor,rut_proveedor,monto,moneda,"
                "descripcion,doc_id,url), entidades, relaciones, documentos. Operadores: =, !=, >, >=, <, <=, "
                "contiene, entre, en. Orden: 'campo' o '-campo'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql_or_filter": {
                        "description": "Filtro JSON (preferido) o sentencia SELECT.",
                        "anyOf": [{"type": "object"}, {"type": "string"}],
                    }
                },
                "required": ["sql_or_filter"],
            },
        },
    },
]


# ───────────────────────── registro de evidencia ─────────────────────────

@dataclass
class Evidencia:
    ref: str
    tipo: Literal["fragmento", "relacion", "registro"]
    texto: str
    doc_id: str | None = None
    titulo: str | None = None
    url: str | None = None
    fecha: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return asdict(self)


class RegistroEvidencia:
    def __init__(self) -> None:
        self.items: dict[str, Evidencia] = {}
        self._claves: dict[str, str] = {}

    def agregar(self, clave: str, **datos: Any) -> Evidencia:
        if clave in self._claves:
            return self.items[self._claves[clave]]
        ref = f"F{len(self.items) + 1}"
        ev = Evidencia(ref=ref, **datos)
        self.items[ref] = ev
        self._claves[clave] = ref
        return ev


# ───────────────────────── ejecución ─────────────────────────

class Herramientas:
    def __init__(self, almacen: Almacen, k: int = 6):
        self.almacen = almacen
        self.k = k
        self.evidencia = RegistroEvidencia()

    def ejecutar(self, nombre: str, argumentos: dict[str, Any]) -> str:
        """Ejecuta una herramienta y devuelve JSON (texto) para el mensaje ``tool``."""
        try:
            if nombre == "search_documents":
                salida = self._buscar(ArgsBuscar.model_validate(argumentos))
            elif nombre == "get_entity_connections":
                salida = self._conexiones(ArgsConexiones.model_validate(argumentos))
            elif nombre == "query_structured_records":
                salida = self._registros(ArgsRegistros.model_validate(argumentos))
            else:
                salida = {"error": f"Herramienta desconocida: {nombre}"}
        except ValidationError as e:
            salida = {"error": "Argumentos inválidos", "detalle": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()][:6]}
        except ConsultaRechazada as e:
            salida = {"error": str(e)}
        return json.dumps(salida, ensure_ascii=False, default=str)

    def _buscar(self, a: ArgsBuscar) -> dict[str, Any]:
        filtros = a.filters.model_dump(exclude_none=True) if a.filters else {}
        resultados, vias = self.almacen.buscar(a.query, filtros, self.k)
        items = []
        for r in resultados:
            ev = self.evidencia.agregar(
                f"frag:{r.fragmento_id}", tipo="fragmento", texto=r.texto, doc_id=r.doc_id, titulo=r.titulo,
                url=r.url, fecha=r.fecha, detalle={"fuente": r.fuente, "vias": r.vias},
            )
            items.append({"ref": ev.ref, "titulo": r.titulo, "fecha": r.fecha, "fuente": r.fuente, "texto": r.texto})
        return {"vias": vias, "resultados": items, "nota": None if items else "Sin resultados para esa búsqueda."}

    def _conexiones(self, a: ArgsConexiones) -> dict[str, Any]:
        candidatos = self.almacen.resolver_entidad(a.entity_name)
        if not candidatos:
            return {"entidad": None, "conexiones": [], "nota": f"No hay ninguna entidad llamada «{a.entity_name}» en el grafo."}
        e = candidatos[0]
        items = []
        for c in self.almacen.conexiones(e["id"], a.depth):
            ev = self.evidencia.agregar(
                f"rel:{c['id']}", tipo="relacion", texto=c["evidencia"], doc_id=c["doc_id"], titulo=c["titulo"],
                url=c["url"], fecha=c["fecha"],
                detalle={"origen": c["origen"], "tipo": c["tipo"], "destino": c["destino"], "confianza": c["confianza"]},
            )
            items.append({
                "ref": ev.ref, "nivel": c["nivel"], "origen": c["origen"], "relacion": c["tipo"],
                "destino": c["destino"], "cita": c["evidencia"], "documento": c["titulo"],
            })
        otros = [f"{x['nombre']} ({x['tipo']})" for x in candidatos[1:]]
        return {"entidad": {"nombre": e["nombre"], "tipo": e["tipo"]}, "conexiones": items,
                "otras_coincidencias": otros or None}

    def _registros(self, a: ArgsRegistros) -> dict[str, Any]:
        q = a.sql_or_filter
        consulta = q.model_dump(exclude_none=True) if isinstance(q, FiltroRegistros) else q
        columnas, filas = self.almacen.consultar(consulta)
        items = []
        for f in filas:
            clave = f"reg:{f.get('id')}" if f.get("id") is not None else f"reg:{json.dumps(f, sort_keys=True, default=str)}"
            doc = self.almacen.documento(f["doc_id"]) if f.get("doc_id") else None
            ev = self.evidencia.agregar(
                clave, tipo="registro", texto=json.dumps(f, ensure_ascii=False, default=str),
                doc_id=f.get("doc_id"), titulo=(doc["titulo"] if doc else "Registro estructurado"),
                url=f.get("url") or (doc["url"] if doc else None), fecha=f.get("fecha"), detalle=f,
            )
            items.append({"ref": ev.ref, **f})
        return {"columnas": columnas, "filas": items, "total": len(items)}
