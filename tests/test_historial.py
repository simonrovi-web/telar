"""Historial y rectificación. Sin red."""
import copy
import json
from pathlib import Path

import pytest

from historial.anotar import HUELLAS, comprobar, diferencias, entradas, huellas, limpiar_retirados, revisar_rectificaciones

DATOS = Path(__file__).parents[1] / "docs" / "data"
L = lambda n: json.loads((DATOS / n).read_text(encoding="utf-8"))  # noqa: E731
REG, H, RS = L("registro.json"), L("historial.json"), L("rectificaciones.json")
PREV = json.loads(HUELLAS.read_text(encoding="utf-8"))
K = next(iter(REG["vinculos"]))


def copia():
    return copy.deepcopy(REG)


def test_lo_publicado_esta_anotado():
    assert comprobar(H, REG, RS, PREV) == []


@pytest.mark.parametrize("cambio, dice", [
    (lambda r: r["vinculos"][K].update(desde="1999"), "cambio en vinculos"),
    (lambda r: r["vinculos"].pop(K), "retiro en vinculos"),
    (lambda r: r["personas"].update(nueva={"nombre": "Ana Prueba"}), "alta en personas"),
    (lambda r: r["personas"]["luz-granier-bulnes"].update(estado="revisada"), "estado"),
])
def test_un_cambio_sin_motivo_se_detecta(cambio, dice):
    r = copia()
    cambio(r)
    assert any(dice in m for m in comprobar(H, r, RS, PREV))


def test_la_fecha_de_actualizacion_sola_no_cuenta():
    r = copia()
    r["personas"]["luz-granier-bulnes"]["actualizado"] = "2030-01-01T00:00:00Z"
    assert comprobar(H, r, RS, PREV) == []


def test_la_anotacion_dice_que_campo_y_no_que_decia():
    r = copia()
    viejo = r["vinculos"][K]["etiqueta"]
    r["vinculos"][K].update(etiqueta="Papel corregido de prueba", detalle="otro")
    (e,) = entradas(diferencias(r, PREV), r, "Motivo de prueba suficiente", "Ana Prueba", "2026-12-01", 99)
    assert e["campos"] == ["detalle", "etiqueta"] and e["n"] == 99 and e["ficha"] == r["vinculos"][K]["personaId"]
    t = json.dumps(e, ensure_ascii=False)
    assert "Papel corregido" not in t and viejo not in t


def test_lo_retirado_no_queda_nombrado():
    r, h = copia(), copy.deepcopy(H)
    quien = "luz-granier-bulnes"
    r["personas"].pop(quien)
    r["vinculos"] = {k: v for k, v in r["vinculos"].items() if v["personaId"] != quien}
    nuevas = entradas(diferencias(r, PREV), r, "Retiro por rectificación aceptada", "Ana Prueba", "2026-12-01", len(h["entradas"]) + 1, "R-2026-001")
    assert {(e["coleccion"], e["cuantos"]) for e in nuevas} == {("personas", 1), ("vinculos", 13)}
    h["entradas"] += nuevas
    limpiar_retirados(h, r)
    assert "granier" not in json.dumps(h, ensure_ascii=False).lower()
    assert "granier" not in json.dumps(huellas(r)).lower()
    rs = {"solicitudes": [{"numero": "R-2026-001", "recibida": "2026-11-30", "estado": "aceptada", "respondida": "2026-12-01", "respuesta": "Se retiró."}]}
    assert comprobar(h, r, rs, huellas(r)) == []


BASE = {"numero": "R-2026-001", "recibida": "2026-11-30", "estado": "recibida"}


@pytest.mark.parametrize("cambio, dice", [
    ({"numero": "1"}, "inválido"), ({"estado": "quizás"}, "desconocido"), ({"estado": "rechazada"}, "sin respuesta"),
    ({"solicitante": "Ana"}, "no se publican"), ({"asunto": "escribir a a@b.cl"}, "correo"), ({"asunto": "soy 12.345.678-5"}, "RUT"),
    ({"ficha": "nadie"}, "ya no existe"), ({"vinculos": ["nada"]}, "ya no existe"),
])
def test_solicitudes_mal_publicadas(cambio, dice):
    assert revisar_rectificaciones({"solicitudes": [BASE]}, REG) == []
    assert any(dice in m for m in revisar_rectificaciones({"solicitudes": [{**BASE, **cambio}]}, REG))


def test_una_anotacion_no_cita_solicitudes_inexistentes():
    h = copy.deepcopy(H)
    h["entradas"][-1]["rectificacion"] = "R-2099-001"
    assert any("inexistente" in m for m in comprobar(h, REG, RS, PREV))
