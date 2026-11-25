"""Control de la evidencia: ¿la frase que sostiene cada afirmación sigue en su fuente?

    python -m evidencia.control            revisa y reescribe docs/data/evidencia.json
    python -m evidencia.control --seco     revisa sin escribir

No usa modelos. Cada frase se guarda como una marca: sus primeras y últimas
palabras, cuántas palabras tiene y su huella SHA-256. Con eso se la reconoce en
la página sin copiar el artículo. Que la frase esté ahí no prueba que sea
cierta: prueba que la fuente lo dice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

RUTA = Path(__file__).resolve().parents[1] / "docs" / "data" / "evidencia.json"
AGENTE = "Mozilla/5.0 (compatible; investigacion/1.0)"
_IGNORAR = {"script", "style", "noscript", "template", "svg"}
_BLOQUES = {"p", "div", "li", "td", "th", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
            "ul", "ol", "dd", "dt", "header", "footer", "blockquote", "figcaption", "span", "strong", "b", "a", "em"}
# Los elementos en línea que parten palabras (p. ej. <b>P</b>iñera) no separan.
_EN_LINEA = {"span", "strong", "b", "a", "em"}


class _Texto(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._fuera = 0

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORAR:
            self._fuera += 1
        elif tag in _BLOQUES - _EN_LINEA:
            self.partes.append(" ")

    def handle_endtag(self, tag):
        if tag in _IGNORAR:
            self._fuera = max(0, self._fuera - 1)
        elif tag in _BLOQUES - _EN_LINEA:
            self.partes.append(" ")

    def handle_data(self, data):
        if not self._fuera:
            self.partes.append(data)


def texto_html(html: str) -> str:
    p = _Texto()
    p.feed(html)
    p.close()
    return "".join(p.partes)


def palabras(texto: str) -> list[str]:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.findall(r"[a-z0-9]+", t)


def huella(ps: list[str]) -> str:
    return hashlib.sha256(" ".join(ps).encode()).hexdigest()[:24]


def marcar(frase: str) -> dict:
    """La marca de una frase: lo justo para volver a encontrarla."""
    ps = palabras(frase)
    if len(ps) < 4:
        raise ValueError("frase demasiado corta para ser evidencia")
    k = min(4, len(ps) // 2)
    return {"ini": " ".join(ps[:k]), "fin": " ".join(ps[-k:]), "n": len(ps), "sha": huella(ps)}


def esta(marca: dict, ps: list[str]) -> bool:
    ini, fin, n = marca["ini"].split(), marca["fin"].split(), marca["n"]
    for i in range(len(ps) - n + 1):
        if ps[i:i + len(ini)] == ini and ps[i + n - len(fin):i + n] == fin and huella(ps[i:i + n]) == marca["sha"]:
            return True
    return False


def descargar(url: str) -> tuple[int, str]:
    import requests
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": AGENTE})
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        return r.status_code, r.text
    except requests.RequestException:
        return 0, ""


def estado_documento(codigo: int, vigentes: int, total: int) -> str:
    if codigo in (404, 410):
        return "caida"
    if codigo != 200:
        return "sin respuesta"          # 403, 429, 5xx o red: no se pudo comprobar, no es una caída
    return "vigente" if vigentes == total else "cambio"


def revisar(ev: dict, bajar=descargar, hoy: str | None = None, espera: float = 1.0) -> list[str]:
    """Actualiza ``ev`` y devuelve lo que cambió de estado."""
    hoy = hoy or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    avisos: list[str] = []
    por_url: dict[str, list[dict]] = {}
    for ms in ev["vinculos"].values():
        for m in ms:
            por_url.setdefault(m["url"], []).append(m)
    for i, (url, ms) in enumerate(por_url.items()):
        if i:
            time.sleep(espera)
        codigo, html = bajar(url)
        ps = palabras(texto_html(html)) if codigo == 200 else []
        halladas = [esta(m, ps) for m in ms] if codigo == 200 else []
        est = estado_documento(codigo, sum(halladas), len(ms))
        d = ev["documentos"].setdefault(url, {"estado": "sin revisar"})
        if d.get("estado") != est:
            avisos.append(f"{d.get('estado')} -> {est}: {url}")
            d["desde"] = hoy
        d.update(estado=est, intento=hoy)
        if codigo == 200:
            d["comprobado"] = hoy
            for m, h in zip(ms, halladas):
                if m.get("vigente") is not h:
                    m["vigente"] = h
                    m["desde"] = hoy
    ev["revisado"] = hoy
    return avisos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ev = json.loads(RUTA.read_text(encoding="utf-8"))
    avisos = revisar(ev)
    for url, d in ev["documentos"].items():
        print(f"{d['estado']:14} {url}")
    total = [m for ms in ev["vinculos"].values() for m in ms]
    vistas = [m for m in total if ev["documentos"][m["url"]].get("comprobado") == ev["revisado"]]
    print(f"{sum(m['vigente'] for m in vistas)}/{len(vistas)} frases presentes, de las comprobadas hoy; "
          f"{len(total) - len(vistas)} sin comprobar hoy")
    for a in avisos:
        print("cambio de estado ·", a)
    if not args.seco:
        RUTA.write_text(json.dumps(ev, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
