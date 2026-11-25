"""Historial del registro: ningún cambio sin motivo.

    python -m historial.anotar --comprobar
    python -m historial.anotar --motivo "La fuente no daba esa fecha" --quien "Nombre Apellido"
    python -m historial.anotar --motivo "…" --quien "…" --rectificacion R-2026-001

Compara docs/data/registro.json con las huellas guardadas y anota cada
diferencia. Dice qué campos cambiaron, no qué decían antes: repetir el dato
viejo anularía la corrección. Lo retirado se anota sin nombre ni identificador.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATOS = Path(__file__).resolve().parents[1] / "docs" / "data"
HUELLAS = Path(__file__).resolve().parent / "huellas.json"
COLECCIONES = ("personas", "organizaciones", "vinculos")
SIN_CONTAR = {"actualizado"}                # cambia con cada edición y no dice nada
ESTADOS = ("recibida", "en revisión", "aceptada", "aceptada en parte", "rechazada")
CERRADAS = ("aceptada", "aceptada en parte", "rechazada")
CAMPOS_RECT = {"numero", "recibida", "estado", "respondida", "ficha", "vinculos", "asunto", "respuesta"}


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def huella(obj: dict) -> dict[str, str]:
    """Una huella por campo: dice cuál cambió sin guardar qué decía."""
    return {k: _h(v) for k, v in sorted(obj.items()) if k not in SIN_CONTAR and not k.startswith("_")}


def huellas(reg: dict) -> dict:
    return {c: {k: huella(v) for k, v in reg[c].items()} for c in COLECCIONES}


def diferencias(reg: dict, previas: dict) -> list[dict]:
    out, ahora = [], huellas(reg)
    for c in COLECCIONES:
        a, b = previas.get(c, {}), ahora[c]
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append({"accion": "alta", "coleccion": c, "id": k, "campos": []})
            elif k not in b:
                out.append({"accion": "retiro", "coleccion": c, "id": None, "campos": []})
            elif a[k] != b[k]:
                out.append({"accion": "cambio", "coleccion": c, "id": k,
                            "campos": sorted(f for f in set(a[k]) | set(b[k]) if a[k].get(f) != b[k].get(f))})
    return out


def entradas(difs: list[dict], reg: dict, motivo: str, quien: str, fecha: str, desde: int, rect: str = "") -> list[dict]:
    out, retiros = [], {}
    for d in difs:
        if d["accion"] == "retiro":
            retiros[d["coleccion"]] = retiros.get(d["coleccion"], 0) + 1
            continue
        e = {"fecha": fecha, "accion": d["accion"], "coleccion": d["coleccion"], "id": d["id"]}
        if d["coleccion"] == "vinculos":
            e["ficha"] = reg["vinculos"][d["id"]]["personaId"]
        elif d["coleccion"] == "personas":
            e["ficha"] = d["id"]
        if d["campos"]:
            e["campos"] = d["campos"]
        out.append(e)
    for c, n in retiros.items():
        out.append({"fecha": fecha, "accion": "retiro", "coleccion": c, "cuantos": n})
    for i, e in enumerate(out):
        e.update(n=desde + i, motivo=motivo, quien=quien)
        if rect:
            e["rectificacion"] = rect
    return out


def limpiar_retirados(h: dict, reg: dict) -> None:
    """Una ficha o vínculo retirado no queda nombrado en anotaciones anteriores."""
    for e in h["entradas"]:
        if e.get("ficha") and e["ficha"] not in reg["personas"]:
            e.pop("ficha")
            e["id"] = None
        if e.get("id") and e["id"] not in reg.get(e["coleccion"], {}):
            e["id"] = None


def revisar_rectificaciones(rs: dict, reg: dict) -> list[str]:
    out, vistos = [], set()
    for r in rs.get("solicitudes", []):
        n = r.get("numero", "")
        if not re.fullmatch(r"R-\d{4}-\d{3}", n) or n in vistos:
            out.append(f"número de solicitud inválido o repetido: {n!r}")
        vistos.add(n)
        extra = set(r) - CAMPOS_RECT
        if extra:
            out.append(f"{n}: campos que no se publican: {sorted(extra)}")
        if r.get("estado") not in ESTADOS:
            out.append(f"{n}: estado desconocido")
        if r.get("estado") in CERRADAS and not (r.get("respondida") and r.get("respuesta")):
            out.append(f"{n}: cerrada sin respuesta fundada")
        if r.get("ficha") and r["ficha"] not in reg["personas"]:
            out.append(f"{n}: nombra una ficha que ya no existe")
        for v in r.get("vinculos", []):
            if v not in reg["vinculos"]:
                out.append(f"{n}: nombra un vínculo que ya no existe")
        if re.search(r"[\w.+-]+@[\w-]+\.\w|\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b", json.dumps(r, ensure_ascii=False)):
            out.append(f"{n}: contiene un correo o un RUT")
    return out


def comprobar(h: dict, reg: dict, rs: dict, previas: dict) -> list[str]:
    out = [f"sin anotar: {d['accion']} en {d['coleccion']}" + (f" ({d['id']}: {', '.join(d['campos'])})" if d["id"] else "")
           for d in diferencias(reg, previas)]
    nums = [e["n"] for e in h["entradas"]]
    if nums != list(range(1, len(nums) + 1)):
        out.append("la numeración del historial no es continua")
    if any(len(e.get("motivo", "")) < 12 or not e.get("quien") for e in h["entradas"]):
        out.append("hay anotaciones sin motivo o sin autor")
    conocidos = {r.get("numero") for r in rs.get("solicitudes", [])}
    out += [f"anotación {e['n']}: solicitud inexistente" for e in h["entradas"] if e.get("rectificacion") and e["rectificacion"] not in conocidos]
    return out + revisar_rectificaciones(rs, reg)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--comprobar", action="store_true")
    ap.add_argument("--motivo", default="")
    ap.add_argument("--quien", default="")
    ap.add_argument("--rectificacion", default="")
    args = ap.parse_args()
    ruta = DATOS / "historial.json"
    reg, h, rs = (json.loads((DATOS / f).read_text(encoding="utf-8")) for f in ("registro.json", "historial.json", "rectificaciones.json"))
    previas = json.loads(HUELLAS.read_text(encoding="utf-8"))
    if not args.comprobar:
        if len(args.motivo.strip()) < 12 or len(args.quien.strip()) < 3:
            print("Hacen falta --motivo (una frase) y --quien.")
            return 2
        nuevas = entradas(diferencias(reg, previas), reg, args.motivo.strip(), args.quien.strip(),
                          datetime.now(timezone.utc).strftime("%Y-%m-%d"), len(h["entradas"]) + 1, args.rectificacion.strip())
        h["entradas"] += nuevas
        previas = huellas(reg)
        HUELLAS.write_text(json.dumps(previas, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        limpiar_retirados(h, reg)
        ruta.write_text(json.dumps(h, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"{len(nuevas)} cambios anotados")
    malos = comprobar(h, reg, rs, previas)
    for m in malos:
        print("MAL ", m)
    print(f"{len(h['entradas'])} anotaciones, {len(malos)} problemas")
    return 1 if malos else 0


if __name__ == "__main__":
    raise SystemExit(main())
