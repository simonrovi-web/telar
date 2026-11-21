"""Exportación al esquema de Telar (personas, organizaciones, vínculos).

El motor no escribe en la base de Telar: genera archivos JSON en una carpeta de
preparación, con ``estado: "en curso"``, para que una persona los revise antes
de cargarlos. Telar exige que todo vínculo vaya de una persona a una contraparte
y lleve al menos un enlace http(s); lo que no cumple queda en ``omitidos``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .almacen import Almacen
from .ontologia import DIMENSION_TELAR, TipoRelacion, slug

TIPO_ORG_POR_PALABRA = [
    ("municipalidad", "organismo publico"), ("ministerio", "organismo publico"), ("servicio", "organismo publico"),
    ("contraloria", "organismo publico"), ("gobierno", "organismo publico"), ("tribunal", "organismo publico"),
    ("corte", "organismo publico"), ("subsecretaria", "organismo publico"),
    ("partido", "partido"), ("fundacion", "fundacion"), ("corporacion", "fundacion"),
    ("universidad", "universidad"), ("diario", "medio"), ("radio", "medio"), ("television", "medio"),
    ("camara", "gremio"), ("asociacion", "gremio"), ("sociedad", "empresa"), ("limitada", "empresa"),
    ("ltda", "empresa"), ("s.a", "empresa"), ("spa", "empresa"), ("constructora", "empresa"),
    ("inversiones", "empresa"), ("consultora", "empresa"), ("iglesia", "organizacion religiosa"),
]
PROCEDENCIA_POR_TIPO_DOC = {
    "contrato": "registro", "decreto": "registro", "resolucion": "registro", "licitacion": "registro",
    "registro": "registro", "acta": "registro", "oficio": "registro",
    "sentencia": "judicial", "fallo": "judicial", "querella": "judicial",
    "declaracion": "declaracion",
}
ETIQUETA = {
    TipoRelacion.ocupa_cargo_en: "Cargo", TipoRelacion.representa_a: "Representante legal",
    TipoRelacion.propietario_de: "Propietario", TipoRelacion.socio_de: "Socio",
    TipoRelacion.adjudica_a: "Adjudicación", TipoRelacion.contrata_con: "Contrato",
    TipoRelacion.paga_a: "Pago", TipoRelacion.dona_a: "Donación", TipoRelacion.miembro_de: "Miembro",
    TipoRelacion.familiar_de: "Parentesco", TipoRelacion.asociado_a: "Relación profesional",
}


def _tipo_org(nombre: str) -> str:
    n = slug(nombre).replace("-", " ") + " " + nombre.lower()
    for palabra, tipo in TIPO_ORG_POR_PALABRA:
        if palabra in n:
            return tipo
    return "otra"


def exportar(almacen: Almacen, destino: str | Path, confianza_minima: float = 0.7) -> dict[str, Any]:
    destino = Path(destino)
    ahora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entidades = {f["id"]: dict(f) for f in almacen.db.execute("SELECT * FROM entidades")}
    personas: dict[str, dict] = {}
    organizaciones: dict[str, dict] = {}
    vinculos: dict[str, dict] = {}
    omitidos: list[dict[str, Any]] = []

    filas = almacen.db.execute(
        "SELECT r.*, d.titulo, d.url, d.fecha, d.tipo AS doc_tipo FROM relaciones r "
        "LEFT JOIN documentos d ON d.id = r.doc_id ORDER BY r.confianza DESC"
    ).fetchall()
    for r in filas:
        tipo = TipoRelacion(r["tipo"])
        a, b = entidades[r["origen"]], entidades[r["destino"]]
        etiqueta = f"{a['nombre']} {tipo.value} {b['nombre']}"
        dim = DIMENSION_TELAR.get(tipo)
        if not dim:
            omitidos.append({"relacion": etiqueta, "motivo": "tipo sin dimensión en Telar"})
            continue
        if r["confianza"] < confianza_minima:
            omitidos.append({"relacion": etiqueta, "motivo": f"confianza {r['confianza']:.2f} < {confianza_minima}"})
            continue
        if not (r["url"] or "").startswith(("http://", "https://")):
            omitidos.append({"relacion": etiqueta, "motivo": "el documento no tiene enlace http(s)"})
            continue
        if a["tipo"] == "persona":
            persona, otra = a, b
        elif b["tipo"] == "persona":
            persona, otra = b, a
        else:
            omitidos.append({"relacion": etiqueta, "motivo": "Telar solo admite vínculos con una persona en un extremo"})
            continue

        pid = slug(persona["nombre"])
        atributos = json.loads(persona["atributos_json"] or "{}")
        personas.setdefault(pid, {
            "nombre": persona["nombre"], "rolPublico": atributos.get("cargo", ""), "pais": "", "ciudad": "",
            "lat": None, "lon": None, "alias": json.loads(persona["alias_json"] or "[]"), "resumen": "",
            "estado": "en curso", "creado": ahora, "actualizado": ahora, "por": "telar_motor",
        })
        if otra["tipo"] == "persona":
            contraparte = f"persona:{slug(otra['nombre'])}"
            personas.setdefault(slug(otra["nombre"]), {
                "nombre": otra["nombre"], "rolPublico": json.loads(otra["atributos_json"] or "{}").get("cargo", ""),
                "pais": "", "ciudad": "", "lat": None, "lon": None, "alias": json.loads(otra["alias_json"] or "[]"),
                "resumen": "", "estado": "en curso", "creado": ahora, "actualizado": ahora, "por": "telar_motor",
            })
        else:
            oid = slug(otra["nombre"])
            contraparte = f"org:{oid}"
            organizaciones.setdefault(oid, {
                "nombre": otra["nombre"], "tipo": _tipo_org(otra["nombre"]), "pais": "", "ciudad": "",
                "lat": None, "lon": None, "resumen": "", "creado": ahora, "por": "telar_motor",
            })

        vid = f"{pid}-{contraparte.replace(':', '-')}-{slug(ETIQUETA[tipo], 20)}"[:90]
        if vid in vinculos:
            vinculos[vid]["fuentes"].append([r["titulo"], r["url"]])
            continue
        vinculos[vid] = {
            "personaId": pid, "contraparte": contraparte, "tipo": dim,
            "etiqueta": atributos.get("cargo") if tipo == TipoRelacion.ocupa_cargo_en and atributos.get("cargo") else ETIQUETA[tipo],
            "desde": r["fecha"] or "", "hasta": "", "monto": "", "moneda": "",
            "registro": PROCEDENCIA_POR_TIPO_DOC.get((r["doc_tipo"] or "").lower(), "prensa"),
            "detalle": f"«{r['evidencia']}»", "fuentes": [[r["titulo"], r["url"]]],
            "creado": ahora, "por": "telar_motor",
            "_motor": {"relacion": tipo.value, "confianza": r["confianza"], "doc_id": r["doc_id"], "revisar": True},
        }

    for carpeta, datos in (("personas", personas), ("organizaciones", organizaciones), ("vinculos", vinculos)):
        (destino / carpeta).mkdir(parents=True, exist_ok=True)
        for k, v in datos.items():
            (destino / carpeta / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    manifiesto = {
        "generado": ahora, "personas": len(personas), "organizaciones": len(organizaciones),
        "vinculos": len(vinculos), "omitidos": omitidos,
        "nota": "Borrador para revisión humana. Nada se cargó en Telar.",
    }
    (destino / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifiesto
