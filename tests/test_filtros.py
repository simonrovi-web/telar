"""Pruebas de los filtros de admisión. Sin red y sin modelo."""
import json
from pathlib import Path

import pytest

from evaluacion.filtros import Persona, admitir_encargo, clave, pagina_es_de, revisar_vinculo

CASOS = json.loads((Path(__file__).parents[1] / "evaluacion" / "casos.json").read_text(encoding="utf-8"))
ANA = Persona(("Ana Prueba Ficticia",), "ficticia", ("Partido Vecinal Ficticio",))
TEXTO = ("Ana Prueba Ficticia milita en el Partido Vecinal Ficticio desde 2010. "
         "Según información no confirmada, Ana Prueba Ficticia es dueña de Salmonera Ventisca. "
         "Ana Prueba Ficticia es hija de Luis Ensayo Ficticio.")


def vinc(**k):
    base = dict(tipo="afiliacion", contraparte="Partido Vecinal Ficticio", contraparte_tipo="organizacion",
                etiqueta="militante", desde="2010", hasta="",
                cita="Ana Prueba Ficticia milita en el Partido Vecinal Ficticio desde 2010")
    return {**base, **k}


def persona(d):
    return Persona(tuple(d["nombres"]), d.get("nomina", ""), tuple(d.get("anclas_fuertes", [])), tuple(d.get("anclas_debiles", [])))


@pytest.mark.parametrize("e", CASOS["encargos"], ids=lambda e: e["nombres"][0])
def test_encargos(e):
    assert admitir_encargo(persona(e)).pasa is (e["esperado"] == "pasar")


@pytest.mark.parametrize("t", [t for t in CASOS["trampas"] if t.get("texto")], ids=lambda t: t["id"])
def test_barrera_de_identidad(t):
    assert pagina_es_de(persona(t["persona"]), t["texto"]).pasa is (t["debe"] == "admitir")


def test_nombre_completo_no_es_subcadena():
    p = Persona(("Simón Rojas",), "ficticia", ("diputado",))
    assert not pagina_es_de(p, "El diputado Simón de Rojas Clemente estudió botánica").pasa
    assert not pagina_es_de(p, "El diputado Simón Rojasviejo").pasa


def test_vinculo_bueno_pasa():
    v = revisar_vinculo(vinc(), TEXTO, ANA)
    assert v.pasa and not v.motivo


@pytest.mark.parametrize("cambio, motivo", [
    (dict(cita="Ana Prueba Ficticia fundó el Partido Vecinal Ficticio"), "literal"),
    (dict(cita="Partido Vecinal"), "literal"),
    (dict(desde="2012"), "año"),
    (dict(contraparte="Salmonera Ventisca"), "cita"),
    (dict(contraparte="Ana Prueba Ficticia"), "consigo misma"),
    (dict(contraparte="Prueba Ficticia"), "consigo misma"),
    (dict(contraparte_tipo="otro"), "ni organización"),
    (dict(tipo="amistad"), "desconocido"),
    (dict(tipo="empresa", contraparte="Salmonera Ventisca", desde="", cita="Ana Prueba Ficticia es dueña de Salmonera Ventisca"), "confirmado"),
    (dict(tipo="cargo", contraparte="Luis Ensayo Ficticio", contraparte_tipo="persona", desde="",
          cita="Ana Prueba Ficticia es hija de Luis Ensayo Ficticio"), "persona"),
])
def test_vinculos_malos(cambio, motivo):
    v = revisar_vinculo(vinc(**cambio), TEXTO, ANA)
    assert not v.pasa and motivo in v.motivo


def test_lo_personal_nunca_se_publica_solo():
    v = revisar_vinculo(vinc(tipo="personal", contraparte="Luis Ensayo Ficticio", contraparte_tipo="persona", desde="",
                             cita="Ana Prueba Ficticia es hija de Luis Ensayo Ficticio"), TEXTO, ANA)
    assert v.pasa and v.motivo == "solo con revisión humana"


def test_clave_estable():
    assert clave(vinc()) == clave(vinc(contraparte="PARTIDO VECINAL FICTICIO"))


def test_candado_de_modelos():
    pytest.importorskip("requests")
    from evaluacion.evaluar import comprobar_modelos
    comprobar_modelos("gemma4:e4b", "qwen3:4b")
    for malo in ("llama3.2:1b", "gpt-5", "claude-opus"):
        with pytest.raises(SystemExit):
            comprobar_modelos(malo)
