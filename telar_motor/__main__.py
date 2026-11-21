"""Línea de comandos (`telar` tras `pip install -e .`, o `python -m telar_motor`):

    telar estado
    telar ingerir documento.txt --titulo "Decreto 114" --url https://… --tipo decreto
    telar registros contratos.json
    telar preguntar "¿Quién firmó el contrato con Constructora Austral?"
    telar exportar exportacion/telar
    telar demo                 # caso ficticio completo de ejemplos/puerto_ventisca
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import Motor
from .ontologia import slug


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="telar", description="Telar · motor Qwen 3 / Ollama")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("estado", help="Comprueba Ollama y muestra el contenido del almacén")
    a = sub.add_parser("ingerir", help="Indexa un .txt y extrae entidades")
    a.add_argument("archivo")
    a.add_argument("--id")
    a.add_argument("--titulo")
    a.add_argument("--url", default="")
    a.add_argument("--fecha", default="")
    a.add_argument("--tipo", default="")
    a.add_argument("--fuente", default="")
    a.add_argument("--sin-extraccion", action="store_true")
    r = sub.add_parser("registros", help="Carga registros estructurados desde un JSON (lista de objetos)")
    r.add_argument("archivo")
    q = sub.add_parser("preguntar", help="Pregunta al agente")
    q.add_argument("pregunta")
    q.add_argument("--json", action="store_true")
    e = sub.add_parser("exportar", help="Genera borradores en el esquema de Telar")
    e.add_argument("destino")
    e.add_argument("--confianza", type=float, default=0.7)
    dm = sub.add_parser("demo", help="Ingiere un caso de ejemplo (corpus.json, registros.json, pregunta.txt) y pregunta")
    dm.add_argument("carpeta", nargs="?", default=str(Path(__file__).resolve().parents[1] / "ejemplos" / "puerto_ventisca"))
    dm.add_argument("--db", default="demo.sqlite", help="Base de la demostración (se reemplaza)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "demo":
        return demo(Path(args.carpeta), Path(args.db))

    m = Motor()
    try:
        if args.cmd == "estado":
            print(json.dumps({"modelo": m.cfg.modelo, "base_url": m.cfg.base_url, "ollama": m.llm.disponible(),
                              **m.almacen.estadisticas()}, ensure_ascii=False, indent=1))
        elif args.cmd == "ingerir":
            ruta = Path(args.archivo)
            texto = ruta.read_text(encoding="utf-8")
            res = m.ingerir(args.id or slug(ruta.stem), args.titulo or ruta.stem, texto, extraer=not args.sin_extraccion,
                            url=args.url, fecha=args.fecha, tipo=args.tipo, fuente=args.fuente)
            print(json.dumps(res.resumen() if res else {"indexado": True}, ensure_ascii=False, indent=1))
            if res and res.descartes:
                print(f"{len(res.descartes)} elementos descartados por verificación:", file=sys.stderr)
                for d in res.descartes[:20]:
                    print(f"  - {d['valor']}: {d['motivo']}", file=sys.stderr)
        elif args.cmd == "registros":
            n = m.almacen.agregar_registros(json.loads(Path(args.archivo).read_text(encoding="utf-8")))
            print(f"{n} registros cargados")
        elif args.cmd == "preguntar":
            resp = m.preguntar(args.pregunta)
            if args.json:
                print(json.dumps(resp.dict(), ensure_ascii=False, indent=1, default=str))
            else:
                print(resp.texto_completo())
                for adv in resp.advertencias:
                    print(f"⚠ {adv}", file=sys.stderr)
        elif args.cmd == "exportar":
            print(json.dumps(m.exportar_telar(args.destino, args.confianza), ensure_ascii=False, indent=1))
    finally:
        m.cerrar()
    return 0


def demo(carpeta: Path, db: Path) -> int:
    import time

    if not (carpeta / "corpus.json").exists():
        print(f"No encuentro {carpeta / 'corpus.json'}", file=sys.stderr)
        return 2
    db.unlink(missing_ok=True)
    m = Motor(ruta_db=db)
    if not m.llm.disponible():
        print(f"Ollama no responde en {m.cfg.base_url} o falta el modelo {m.cfg.modelo}.\n"
              f"  ollama serve   ·   ollama pull {m.cfg.modelo}   ·   ollama pull {m.cfg.modelo_embeddings}", file=sys.stderr)
        return 1
    print(f"Modelo {m.cfg.modelo} · embeddings {m.cfg.modelo_embeddings or '(ninguno)'} · base {db}\n")
    try:
        t0 = time.monotonic()
        for d in json.loads((carpeta / "corpus.json").read_text(encoding="utf-8")):
            res = m.ingerir(d["id"], d["titulo"], d["texto"], url=d.get("url", ""), fecha=d.get("fecha", ""),
                            tipo=d.get("tipo", ""), fuente=d.get("fuente", ""))
            print(f"· {d['titulo'][:70]}: {len(res.entidades)} entidades, {len(res.relaciones)} relaciones, "
                  f"{len(res.descartes)} descartes ({res.segundos:.0f} s)")
        if (carpeta / "registros.json").exists():
            m.almacen.agregar_registros(json.loads((carpeta / "registros.json").read_text(encoding="utf-8")))
        pregunta = (carpeta / "pregunta.txt").read_text(encoding="utf-8").strip() if (carpeta / "pregunta.txt").exists() \
            else input("Pregunta: ")
        print(f"\n¿{pregunta.lstrip('¿')}\n")
        r = m.preguntar(pregunta)
        print(r.texto_completo())
        for adv in r.advertencias:
            print(f"⚠ {adv}")
        print(f"\nTotal: {time.monotonic() - t0:.0f} s · {m.almacen.estadisticas()}")
    finally:
        m.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
