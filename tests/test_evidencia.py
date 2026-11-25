"""Evidencia: marcas, control y coherencia con el registro. Sin red."""
import copy
import json
from pathlib import Path

import pytest

from evidencia.control import esta, estado_documento, marcar, palabras, revisar, texto_html

DATOS = Path(__file__).parents[1] / "docs" / "data"
EV = json.loads((DATOS / "evidencia.json").read_text(encoding="utf-8"))
REG = json.loads((DATOS / "registro.json").read_text(encoding="utf-8"))
FRASE = "Ana Prueba presidió la Fundación Imaginaria entre 2010 y 2014."


def test_la_marca_encuentra_la_frase_y_nada_parecido():
    m = marcar(FRASE)
    assert esta(m, palabras("Antes. " + FRASE + " Después."))
    assert esta(m, palabras("ANA  PRUEBA presidio la fundacion imaginaria, entre 2010 y 2014"))
    assert not esta(m, palabras(FRASE.replace("2010", "2011")))
    assert not esta(m, palabras(FRASE.replace("presidió", "no presidió")))
    assert not esta(m, [])
    with pytest.raises(ValueError):
        marcar("Muy corta")


def test_texto_html():
    h = "<p>Sebasti&aacute;n <b>P</b>rueba</p><script>x='oculto'</script><td>uno</td><td>dos</td>"
    assert palabras(texto_html(h)) == ["sebastian", "prueba", "uno", "dos"]


def test_un_bloqueo_no_es_una_caida():
    assert estado_documento(403, 0, 1) == estado_documento(0, 0, 1) == estado_documento(503, 0, 1) == "sin respuesta"
    assert estado_documento(404, 0, 1) == estado_documento(410, 0, 1) == "caida"
    assert estado_documento(200, 1, 1) == "vigente"
    assert estado_documento(200, 0, 1) == "cambio"


def test_el_control_anota_sin_olvidar():
    ev = {"documentos": {}, "vinculos": {"v": [{"url": "https://x.cl", "cubre": ["papel"], **marcar(FRASE)}]}}
    assert revisar(ev, lambda u: (200, "<p>" + FRASE + "</p>"), "2026-01-01", 0)
    assert ev["vinculos"]["v"][0]["vigente"] is True and ev["documentos"]["https://x.cl"]["comprobado"] == "2026-01-01"
    assert revisar(ev, lambda u: (403, ""), "2026-01-08", 0)
    assert ev["vinculos"]["v"][0]["vigente"] is True                                  # no se pudo mirar: no cambia
    assert ev["documentos"]["https://x.cl"]["comprobado"] == "2026-01-01"
    revisar(ev, lambda u: (200, "<p>otra cosa</p>"), "2026-01-15", 0)
    assert ev["vinculos"]["v"][0]["vigente"] is False and ev["documentos"]["https://x.cl"]["estado"] == "cambio"
    antes = copy.deepcopy(ev["vinculos"])
    assert revisar(ev, lambda u: (200, "<p>otra cosa</p>"), "2026-01-22", 0) == [] and ev["vinculos"] == antes


def test_el_control_comprueba_de_quien_habla_la_pagina():
    reg = {"vinculos": {"v": {"personaId": "p"}}}
    ident = {"personas": {"p": {"nombres": ["Ana Prueba"], "nomina": {"url": "https://n.cl"}, "fuertes": ["Fundación Imaginaria"]}}}
    ev = {"documentos": {}, "vinculos": {"v": [{"url": "https://x.cl", "cubre": ["papel"], **marcar(FRASE)}]}}
    revisar(ev, lambda u: (200, "<p>" + FRASE + "</p>"), "2026-01-01", 0, reg, ident)
    assert ev["identidad"]["p"]["https://x.cl"]["ok"] is True
    avisos = revisar(ev, lambda u: (200, "<p>Ana Prueba vende flores.</p>"), "2026-01-08", 0, reg, ident)
    assert ev["identidad"]["p"]["https://x.cl"]["ok"] is False and any("NO comprobada" in a for a in avisos)
    revisar(ev, lambda u: (403, ""), "2026-01-15", 0, reg, ident)
    assert ev["identidad"]["p"]["https://x.cl"]["fecha"] == "2026-01-08"


def test_cada_afirmacion_tiene_frase_de_una_fuente_que_cita():
    assert set(EV["vinculos"]) == set(REG["vinculos"])
    for k, ms in EV["vinculos"].items():
        urls = {s[1] for s in REG["vinculos"][k]["fuentes"]}
        assert ms and {m["url"] for m in ms} == urls, k
        assert any("papel" in m["cubre"] for m in ms), k
        for m in ms:
            assert m["n"] >= 4 and len(m["sha"]) == 24 and m["url"] in EV["documentos"], k


def test_no_se_copia_prensa():
    for ms in EV["vinculos"].values():
        for m in ms:
            if "cita" in m:
                assert ("bcn.cl" in m["url"] or "/transparencia/" in m["url"]) and m["n"] <= 40
                assert marcar(m["cita"])["sha"] == m["sha"]
            else:
                assert len(" ".join(m["salto"]).split()) <= 8


def test_no_se_presenta_como_revision_humana():
    assert "sin revisión humana" in EV["seleccion"]
