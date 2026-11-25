"""Identidad: la barrera contra homónimos y los datos publicados. Sin red."""
import json
from pathlib import Path

import pytest

from identidad.barrera import Persona, admitir, de_dict, pagina_es_de, revisar

DATOS = Path(__file__).parents[1] / "docs" / "data"
ID = json.loads((DATOS / "identidad.json").read_text(encoding="utf-8"))
REG = json.loads((DATOS / "registro.json").read_text(encoding="utf-8"))
NOM = "https://nomina.example/lista"
FIC = Persona(("Simón Rojas Fuentealba", "Simón Rojas"), NOM, ("Unión Austral Ficticia", "distrito 99"),
              ("diputado", "Cámara de Diputadas y Diputados"))


@pytest.mark.parametrize("p, motivo", [
    (Persona(("Simon",), NOM, ("x",)), "incompleto"),
    (Persona(("Simon Rojas",)), "nómina"),
    (Persona(("Simon Rojas",), "una lista"), "nómina"),
    (Persona(("Simón Rojas",), NOM), "distingan"),
    (Persona(("Simón Rojas",), NOM, (), ("diputado",)), "distingan"),
])
def test_encargos_que_se_rechazan(p, motivo):
    v = admitir(p)
    assert not v.pasa and motivo in v.motivo


def test_encargo_completo():
    assert admitir(FIC).pasa


@pytest.mark.parametrize("texto", [
    "San Simón de Rojas estudió en Salamanca y fue confesor de la reina.",
    "El diputado Simón de Rojas Clemente y Rubio fue bibliotecario del Jardín Botánico.",
    "Simón Rojas es ceramista. Contó que su vecino, diputado, le compró una pieza.",
    "El diputado Simón Rojas Norlandés presidió el Partido Agrario en el distrito 99.",
    "Simón Rojas de la Fuente, del distrito 99, abrió un taller.",
    "Simón Rojasviejo, distrito 99.",
])
def test_homonimos_rechazados(texto):
    assert not pagina_es_de(FIC, texto).pasa


@pytest.mark.parametrize("texto", [
    "Simón Rojas Fuentealba, militante de Unión Austral Ficticia, votó a favor.",
    "SIMON ROJAS FUENTEALBA (distrito 99)",
    "El diputado Simón Rojas, del distrito 99, intervino.",
    "Simón Rojas Fuentealba es diputado en la Cámara de Diputadas y Diputados.",
])
def test_la_persona_pasa(texto):
    assert pagina_es_de(FIC, texto).pasa


def test_los_homonimos_declarados_no_pasan_por_la_persona():
    for k, d in ID["personas"].items():
        for h in d["noConfundir"]:
            assert not pagina_es_de(de_dict(d), f"{h['nombre']} dio una charla en Codelco sobre su trabajo.").pasa, (k, h["nombre"])


def test_registro_publicado_sin_problemas_de_identidad():
    assert revisar(ID, REG) == []


@pytest.mark.parametrize("cambio, dice", [
    (lambda i: i["personas"].pop("luz-granier-bulnes"), "sin identidad"),
    (lambda i: i["personas"]["luz-granier-bulnes"].update(fuertes=[]), "distingan"),
    (lambda i: i["personas"]["luz-granier-bulnes"].update(rut="1-9"), "no se guardan"),
    (lambda i: i["personas"]["luz-granier-bulnes"]["nombres"].append("Tamara Agnic"), "comparten la forma"),
    (lambda i: i["personas"]["luz-granier-bulnes"]["ids"][0].update(id="Q306"), "comparten el identificador"),
    (lambda i: i["personas"]["luz-granier-bulnes"]["ids"][0].update(por=""), "sin decir con qué"),
])
def test_la_revision_detecta(cambio, dice):
    i = json.loads(json.dumps(ID))
    cambio(i)
    assert any(dice in m for m in revisar(i, REG))


def test_no_se_presenta_como_revision_humana():
    assert "sin revisión humana" in ID["seleccion"]
