"""Pruebas del motor Qwen 3 / Ollama sobre un caso ficticio de contratación municipal.

Dos niveles:

* Pruebas de plomería (siempre corren, sin red): un doble del cliente de
  OpenAI devuelve respuestas guionizadas y se comprueba reintentos, interruptor
  de razonamiento, reparación de JSON, verificación de evidencias, búsqueda
  híbrida, grafo, consultas de solo lectura, bucle del agente, citas y
  exportación a Telar.
* Prueba de integración (solo si Ollama responde y el modelo está descargado):
  el pipeline completo con Qwen 3 real, con tiempos por etapa.

Ejecutar desde la raíz del repositorio:
    python -m pytest tests/test_pipeline.py -q            # plomería (+ integración si hay Ollama)
    python -m pytest tests/test_pipeline.py -q -s -k real # solo integración, con tiempos
    python tests/test_pipeline.py                          # demostración con Ollama real

El corpus está en ejemplos/puerto_ventisca/. TODO ES FICTICIO: Puerto Ventisca, la Región de Los Fiordes, sus
autoridades, empresas y documentos no existen. Los enlaces usan example.org.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("pydantic")

from telar_motor import Motor  # noqa: E402
from telar_motor.almacen import Almacen, ConsultaRechazada  # noqa: E402
from telar_motor.config import CFG  # noqa: E402
from telar_motor.entity_extractor import EntityExtractor, cita_literal  # noqa: E402
from telar_motor.llm_client import LLMClient  # noqa: E402
from telar_motor.ontologia import normalizar  # noqa: E402
from telar_motor.search_agent import verificar_respuesta  # noqa: E402
from telar_motor.telar_export import exportar  # noqa: E402

# ═══════════════════════════ corpus ficticio ═══════════════════════════

EJEMPLO = Path(__file__).resolve().parents[1] / "ejemplos" / "puerto_ventisca"
CORPUS = json.loads((EJEMPLO / "corpus.json").read_text(encoding="utf-8"))
REGISTROS = json.loads((EJEMPLO / "registros.json").read_text(encoding="utf-8"))
PREGUNTA = (EJEMPLO / "pregunta.txt").read_text(encoding="utf-8").strip()

# ═══════════════════════════ doble del cliente OpenAI ═══════════════════════════


def _respuesta(contenido="", herramientas=None, fin="stop", razonamiento=None):
    tcs = None
    if herramientas:
        tcs = [
            SimpleNamespace(id=f"call_{i}", type="function",
                            function=SimpleNamespace(name=n, arguments=json.dumps(a, ensure_ascii=False)))
            for i, (n, a) in enumerate(herramientas)
        ]
    msg = SimpleNamespace(content=contenido, tool_calls=tcs, reasoning=razonamiento, model_extra={})
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="tool_calls" if tcs else fin)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50), model="qwen3:doble",
    )


class Error400(Exception):
    status_code = 400


class APIConnectionError(Exception):  # mismo nombre que la del SDK: se trata como transitoria
    pass


def embedding_hash(textos, dims=64):
    """Embeddings deterministas por hashing de términos (suficiente para probar la vía vectorial)."""
    salida = []
    for t in textos:
        v = [0.0] * dims
        for tok in re.findall(r"\w{3,}", normalizar(t)):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % dims] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        salida.append([x / n for x in v])
    return salida


class ClienteDoble:
    """Imita ``openai.OpenAI``: chat.completions.create, embeddings.create, models.list."""

    def __init__(self, guion):
        # guion: función (params → respuesta) o secuencia/iterador de respuestas
        if callable(guion):
            self._siguiente = guion
        else:
            it = iter(guion)
            self._siguiente = lambda _params: next(it)
        self.llamadas: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._crear))
        self.embeddings = SimpleNamespace(create=self._embeber)
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="qwen3:doble")]))

    def _crear(self, **params):
        self.llamadas.append(params)
        r = self._siguiente(params)
        if isinstance(r, Exception):
            raise r
        return r

    def _embeber(self, model, input):
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=e) for i, e in enumerate(embedding_hash(input))])


CFG_PRUEBA = CFG.con(modelo="qwen3:doble", espera_base_s=0.0, reintentos=2, modo_json="schema", modelo_embeddings="hash")


def extraccion_decreto(params):
    """Respuesta de extracción para el decreto, con errores sembrados que la verificación debe descartar."""
    return _respuesta(json.dumps({
        "entities": [
            {"id": "e1", "name": "Municipalidad de Puerto Ventisca", "type": "organizacion"},
            {"id": "e2", "name": "Constructora Austral Ventisca Limitada", "type": "organizacion",
             "attributes": {"rut": "76.555.120-K"}},
            {"id": "e3", "name": "Abelardo Sotomayor Lienlaf", "type": "persona", "aliases": ["Sotomayor"],
             "attributes": {"cargo": "Alcalde"}},
            {"id": "e4", "name": "Decreto Alcaldicio N° 2.214", "type": "documento"},
            {"id": "e5", "name": "$ 1.240.000.000", "type": "monto_contrato", "attributes": {"moneda": "CLP"}},
            {"id": "e6", "name": "14 de marzo de 2025", "type": "fecha"},
            {"id": "e7", "name": "Juan Pérez Soto", "type": "persona"},  # alucinada: no está en el texto
        ],
        "relations": [
            {"source": "e1", "relation_type": "adjudica_a", "target": "e2", "confidence": 0.95,
             "evidence_snippet": "Adjudícase la licitación pública ID 4471-12-LP25 a Constructora Austral Ventisca Limitada"},
            {"source": "e3", "relation_type": "ocupa_cargo_en", "target": "e1", "confidence": 0.95,
             "evidence_snippet": "Abelardo Sotomayor Lienlaf, Alcalde de la Municipalidad de Puerto Ventisca"},
            {"source": "e3", "relation_type": "firma", "target": "e4", "confidence": 0.9,
             "evidence_snippet": "Firmado: Abelardo Sotomayor Lienlaf"},
            {"source": "e4", "relation_type": "tiene_monto", "target": "e5", "confidence": 0.9,
             "evidence_snippet": "por un monto total de $ 1.240.000.000"},
            {"source": "e4", "relation_type": "ocurre_en_fecha", "target": "e6", "confidence": 0.9,
             "evidence_snippet": "Puerto Ventisca, 14 de marzo de 2025"},
            # evidencia parafraseada: debe descartarse
            {"source": "e3", "relation_type": "propietario_de", "target": "e2", "confidence": 0.8,
             "evidence_snippet": "el alcalde es dueño de la constructora"},
            # tipos incompatibles: una organización no firma
            {"source": "e2", "relation_type": "firma", "target": "e4", "confidence": 0.9,
             "evidence_snippet": "Firmado: Abelardo Sotomayor Lienlaf"},
            {"source": "e7", "relation_type": "miembro_de", "target": "e1", "confidence": 0.9,
             "evidence_snippet": "Firmado: Abelardo Sotomayor Lienlaf"},
        ],
    }, ensure_ascii=False))


def extraccion_otros(params):
    texto = params["messages"][-1]["content"]
    if "Mirta Echaurren" in texto and "cuñada" in texto:
        return _respuesta(json.dumps({
            "entities": [
                {"id": "e1", "name": "Gaspar Ugarte Rivas", "type": "persona", "aliases": ["Ugarte"],
                 "attributes": {"cargo": "concejal"}},
                {"id": "e2", "name": "Mirta Echaurren Paillalef", "type": "persona"},
                {"id": "e3", "name": "Abelardo Sotomayor Lienlaf", "type": "persona", "aliases": ["Sotomayor"]},
                {"id": "e4", "name": "Contraloría Regional de Los Fiordes", "type": "organizacion"},
            ],
            "relations": [
                {"source": "e2", "relation_type": "familiar_de", "target": "e3", "confidence": 0.6,
                 "evidence_snippet": "Mirta Echaurren Paillalef, es cuñada del alcalde Abelardo Sotomayor Lienlaf"},
            ],
        }, ensure_ascii=False))
    if "representada legalmente por" in texto:
        return _respuesta(json.dumps({
            "entities": [
                {"id": "e1", "name": "Mirta Echaurren Paillalef", "type": "persona"},
                {"id": "e2", "name": "Constructora Austral Ventisca Limitada", "type": "organizacion"},
                {"id": "e3", "name": "Municipalidad de Puerto Ventisca", "type": "organizacion"},
            ],
            "relations": [
                {"source": "e1", "relation_type": "representa_a", "target": "e2", "confidence": 0.95,
                 "evidence_snippet": "Constructora Austral Ventisca Limitada, representada legalmente por doña Mirta Echaurren Paillalef"},
                {"source": "e3", "relation_type": "contrata_con", "target": "e2", "confidence": 0.9,
                 "evidence_snippet": "se celebra el presente contrato para la ejecución de la obra"},
            ],
        }, ensure_ascii=False))
    return _respuesta('{"entities": [], "relations": []}')


def guion_extraccion(params):
    if "DECRETO ALCALDICIO" in str(params["messages"]):
        return extraccion_decreto(params)
    return extraccion_otros(params)


@pytest.fixture
def motor_poblado(tmp_path):
    m = Motor(CFG_PRUEBA, cliente=ClienteDoble(guion_extraccion), ruta_db=tmp_path / "motor.sqlite")
    for d in CORPUS:
        m.ingerir(d["id"], d["titulo"], d["texto"], url=d["url"], fecha=d["fecha"], tipo=d["tipo"], fuente=d["fuente"])
    m.almacen.agregar_registros(REGISTROS)
    yield m
    m.cerrar()


# ═══════════════════════════ cliente LLM ═══════════════════════════


def test_interruptor_de_razonamiento_y_campos_no_soportados():
    doble = ClienteDoble([_respuesta("hola"), _respuesta("hola")])
    llm = LLMClient(CFG_PRUEBA, cliente=doble)
    llm.chat([{"role": "user", "content": "x"}], modo="extraccion", razonar=False)
    llm.chat([{"role": "user", "content": "x"}], modo="sintesis", razonar=True, herramientas=[{"type": "function"}])
    apagado, encendido = doble.llamadas
    assert apagado["extra_body"] == {"reasoning_effort": "none"}
    assert apagado["temperature"] == 0.1
    assert encendido["extra_body"] == {"reasoning_effort": "medium"}
    assert encendido["temperature"] == 0.3
    assert encendido["max_tokens"] == CFG_PRUEBA.max_tokens_sintesis + CFG_PRUEBA.max_tokens_razonamiento
    for p in doble.llamadas:
        assert not {"tool_choice", "n", "logit_bias", "user"} & set(p), "Ollama no soporta esos campos"


def test_razonamiento_separado_y_etiquetas_think_limpiadas():
    doble = ClienteDoble([_respuesta("<think>pienso…</think>Respuesta final", razonamiento=None)])
    r = LLMClient(CFG_PRUEBA, cliente=doble).chat([{"role": "user", "content": "x"}])
    assert r.contenido == "Respuesta final"
    assert r.razonamiento == "pienso…"


def test_reintentos_ante_errores_transitorios():
    doble = ClienteDoble([APIConnectionError("caído"), APIConnectionError("caído"), _respuesta("ok")])
    r = LLMClient(CFG_PRUEBA, cliente=doble).chat([{"role": "user", "content": "x"}])
    assert r.contenido == "ok" and len(doble.llamadas) == 3


def test_truncado_por_razonamiento_reintenta_con_mas_margen():
    doble = ClienteDoble([_respuesta("", fin="length"), _respuesta("completo")])
    r = LLMClient(CFG_PRUEBA, cliente=doble).chat([{"role": "user", "content": "x"}], razonar=True)
    assert r.contenido == "completo"
    assert doble.llamadas[1]["max_tokens"] == 2 * doble.llamadas[0]["max_tokens"]


def test_json_schema_rechazado_cae_a_json_object_y_repara():
    from telar_motor.ontologia import Extraccion

    doble = ClienteDoble([
        Error400("response_format json_schema not supported"),
        _respuesta("```json\n{\"entities\": [{\"id\": \"e1\", \"name\": \"X\"}]}\n```"),  # falta type
        _respuesta('{"entities": [{"id": "e1", "name": "X", "type": "persona"}], "relations": []}'),
    ])
    llm = LLMClient(CFG_PRUEBA, cliente=doble)
    ext, _ = llm.estructurado([{"role": "user", "content": "x"}], Extraccion)
    assert ext.entities[0].name == "X"
    assert doble.llamadas[0]["response_format"]["type"] == "json_schema"
    assert doble.llamadas[1]["response_format"] == {"type": "json_object"}
    assert "no cumple el esquema" in doble.llamadas[2]["messages"][-1]["content"]


def test_ollama_antiguo_sin_reasoning_effort():
    doble = ClienteDoble([Error400("unknown field reasoning_effort"), _respuesta("ok"), _respuesta("ok")])
    llm = LLMClient(CFG_PRUEBA, cliente=doble)
    llm.chat([{"role": "user", "content": "x"}])
    llm.chat([{"role": "user", "content": "x"}])
    assert "extra_body" not in doble.llamadas[1] and "extra_body" not in doble.llamadas[2]


def test_esquema_sin_referencias_y_errores_definitivos_legibles():
    from telar_motor.llm_client import LLMError, esquema_plano
    from telar_motor.ontologia import Extraccion

    assert "$ref" not in json.dumps(esquema_plano(Extraccion))

    class NotFoundError(Exception):
        status_code = 404

    llm = LLMClient(CFG_PRUEBA, cliente=ClienteDoble([NotFoundError("model 'qwen3:14b' not found")]))
    with pytest.raises(LLMError, match="not found"):
        llm.chat([{"role": "user", "content": "x"}])


# ═══════════════════════════ extracción ═══════════════════════════


def test_cita_literal_tolera_formato_pero_no_parafrasis():
    t = CORPUS[2]["texto"]
    assert cita_literal("declaró que \"no participé en la comisión evaluadora\"", t)
    assert cita_literal("presentó una denuncia ... por la adjudicación de la Costanera Sur", t)
    assert not cita_literal("el alcalde admitió el parentesco", t)


def test_extraccion_descarta_lo_que_el_texto_no_sostiene():
    llm = LLMClient(CFG_PRUEBA, cliente=ClienteDoble(guion_extraccion))
    d = CORPUS[0]
    res = EntityExtractor(llm, CFG_PRUEBA).extraer(d["texto"], doc_id=d["id"], titulo=d["titulo"])
    nombres = {e.nombre for e in res.entidades}
    assert "Abelardo Sotomayor Lienlaf" in nombres
    assert "Juan Pérez Soto" not in nombres
    tipos = {(r.origen, r.tipo, r.destino) for r in res.relaciones}
    assert ("organizacion:municipalidad-de-puerto-ventisca", "adjudica_a",
            "organizacion:constructora-austral-ventisca-limitada") in tipos
    assert not any(r.tipo == "propietario_de" for r in res.relaciones)
    motivos = {d["motivo"] for d in res.descartes}
    assert "no aparece en el texto" in motivos
    assert "la evidencia no es una cita literal" in motivos
    assert any(m.startswith("tipos incompatibles") for m in motivos)
    assert len(res.relaciones) == 5


def test_fusion_de_entidades_entre_documentos(motor_poblado):
    filas = motor_poblado.almacen.resolver_entidad("Sotomayor")
    assert len(filas) == 1 and filas[0]["id"] == "persona:abelardo-sotomayor-lienlaf"


# ═══════════════════════════ almacén ═══════════════════════════


def test_busqueda_hibrida_fts_y_vectorial(motor_poblado):
    res, vias = motor_poblado.almacen.buscar("¿quién es la representante legal de la constructora?")
    assert vias == ["fts5", "sqlite-vec"] or vias == ["fts5"]  # sqlite-vec puede no estar instalado
    assert res and res[0].doc_id in {"contrato-costanera-2025", "prensa-faro-2025-06-10"}
    res, _ = motor_poblado.almacen.buscar("costanera", {"tipo": "resolucion"})
    assert {r.doc_id for r in res} == {"contraloria-418-2025"}
    res, _ = motor_poblado.almacen.buscar("adjudicación", {"fecha_desde": "2025-06-01"})
    assert all(r.fecha >= "2025-06-01" for r in res)


def test_busqueda_sin_embeddings_cae_a_lexica(tmp_path):
    def roto(_):
        raise RuntimeError("modelo de embeddings no descargado")

    a = Almacen(tmp_path / "x.sqlite", embedder=roto)
    a.agregar_documento("d", "Decreto", CORPUS[0]["texto"])
    res, vias = a.buscar("licitación costanera")
    assert vias == ["fts5"] and res
    assert any("solo léxica" in x for x in a.avisos)
    a.cerrar()


def test_conexiones_con_profundidad(motor_poblado):
    alm = motor_poblado.almacen
    nivel1 = alm.conexiones("persona:mirta-echaurren-paillalef", 1)
    assert {c["destino"] for c in nivel1} >= {"Constructora Austral Ventisca Limitada", "Abelardo Sotomayor Lienlaf"}
    nivel2 = alm.conexiones("persona:mirta-echaurren-paillalef", 2)
    assert any(c["tipo"] == "adjudica_a" and c["nivel"] == 2 for c in nivel2)
    assert all(c["evidencia"] and c["doc_id"] for c in nivel2)


def test_consultas_estructuradas_de_solo_lectura(motor_poblado):
    alm = motor_poblado.almacen
    _, filas = alm.consultar({"tabla": "registros", "donde": [{"campo": "proveedor", "op": "contiene", "valor": "austral"}],
                              "orden": "-monto"})
    assert [f["id"] for f in filas] == ["4471-12-LP25", "4471-31-LE24"]
    _, filas = alm.consultar("SELECT proveedor, sum(monto) AS total FROM registros GROUP BY proveedor ORDER BY total DESC")
    assert filas[0]["total"] == 1240000000 + 186500000
    for malicioso in [
        "DELETE FROM registros",
        "SELECT * FROM registros; DROP TABLE registros",
        "SELECT name FROM sqlite_master",
        "SELECT load_extension('x')",
        "SELECT texto FROM documentos",           # columna fuera de la lista blanca
        "WITH x AS (SELECT 1) SELECT * FROM x; PRAGMA writable_schema=1",
    ]:
        with pytest.raises(ConsultaRechazada):
            alm.consultar(malicioso)
    with pytest.raises(ConsultaRechazada):
        alm.consultar({"tabla": "registros", "donde": [{"campo": "monto; DROP", "op": "=", "valor": 1}]})
    assert alm.estadisticas()["registros"] == 3  # nada se borró


# ═══════════════════════════ agente ═══════════════════════════


def test_agente_usa_herramientas_y_cita_fuentes_exactas(motor_poblado):
    pasos = iter([
        _respuesta(herramientas=[("search_documents", {"query": "representante legal Constructora Austral Ventisca"})]),
        _respuesta(herramientas=[("get_entity_connections", {"entity_name": "Mirta Echaurren", "depth": 1}),
                                 ("query_structured_records", {"sql_or_filter": {
                                     "tabla": "registros",
                                     "donde": [{"campo": "proveedor", "op": "contiene", "valor": "Austral"}],
                                     "orden": "-monto"}})]),
    ])

    def guion(params):
        try:
            return next(pasos)
        except StopIteration:
            # respuesta final: construida con las referencias que el agente recibió
            tool_msgs = [json.loads(m["content"]) for m in params["messages"] if m["role"] == "tool"]
            ref_frag = next(r["ref"] for r in tool_msgs[0]["resultados"] if "representada legalmente" in r["texto"])
            ref_rel = next(c["ref"] for c in tool_msgs[1]["conexiones"] if c["relacion"] == "representa_a")
            ref_reg = tool_msgs[2]["filas"][0]["ref"]
            return _respuesta(
                f"Mirta Echaurren Paillalef es la representante legal de Constructora Austral Ventisca Limitada: "
                f"el contrato dice «representada legalmente por doña Mirta Echaurren Paillalef» [{ref_frag}, {ref_rel}]. "
                f"La empresa tiene dos licitaciones con la municipalidad; la mayor suma $ 1.240.000.000 [{ref_reg}]. "
                f"Un medio afirmó que es su hermana gemela [F99]."
            )

    motor_poblado.agente.llm = LLMClient(CFG_PRUEBA, cliente=ClienteDoble(guion))
    r = motor_poblado.preguntar("¿Quién representa a la constructora que ganó la Costanera Sur y cuánto ha contratado?")
    assert [p.herramienta for p in r.pasos] == ["search_documents", "get_entity_connections", "query_structured_records"]
    assert "F99" not in r.respuesta and r.verificacion["referencias_inventadas"] == ["F99"]
    assert r.verificacion["citas_textuales_verificadas"] == 1
    assert {c.doc_id for c in r.citas} >= {"contrato-costanera-2025", "decreto-2214-2025"}
    assert all(c.url and c.url.startswith("https://example.org/") for c in r.citas)
    assert "Fuentes" in r.texto_completo() and "example.org/puerto-ventisca/contratos" in r.fuentes
    assert any("inexistentes" in a for a in r.advertencias)


def test_agente_recuerda_usar_herramientas(motor_poblado):
    guion = iter([
        _respuesta("Creo que la empresa es de Santiago."),                  # sin herramientas
        _respuesta(herramientas=[("search_documents", {"query": "única oferta licitación"})]),
        _respuesta("La licitación recibió una única oferta [F1]."),
    ])
    motor_poblado.agente.llm = LLMClient(CFG_PRUEBA, cliente=ClienteDoble(guion))
    r = motor_poblado.preguntar("¿Cuántas ofertas recibió la licitación?")
    assert r.pasos and r.citas and r.citas[0].doc_id == "contraloria-418-2025"
    assert r.verificacion["verificada"]


def test_verificador_detecta_cita_textual_falsa():
    from telar_motor.herramientas import Evidencia

    reg = {"F1": Evidencia("F1", "fragmento", CORPUS[2]["texto"], "prensa", "El Faro", None, None)}
    _, _, v = verificar_respuesta("El alcalde dijo «yo no conozco a esa empresa en absoluto» [F1].", reg)
    assert v["citas_textuales_no_verificadas"] and not v["verificada"]


# ═══════════════════════════ exportación a Telar ═══════════════════════════


def test_exportacion_a_telar(motor_poblado, tmp_path):
    man = exportar(motor_poblado.almacen, tmp_path / "telar", confianza_minima=0.7)
    vinculos = [json.loads(p.read_text()) for p in (tmp_path / "telar" / "vinculos").glob("*.json")]
    assert man["personas"] >= 2 and vinculos
    assert all(v["fuentes"][0][1].startswith("https://") for v in vinculos)
    assert all(v["contraparte"].split(":")[0] in {"persona", "org"} for v in vinculos)
    rep = next(v for v in vinculos if v["personaId"] == "mirta-echaurren-paillalef" and v["tipo"] == "empresa")
    assert rep["contraparte"] == "org:constructora-austral-ventisca-limitada" and rep["registro"] == "registro"
    org = json.loads((tmp_path / "telar" / "organizaciones" / "municipalidad-de-puerto-ventisca.json").read_text())
    assert org["tipo"] == "organismo publico"
    motivos = " ".join(o["motivo"] for o in man["omitidos"])
    assert "una persona en un extremo" in motivos      # municipalidad → constructora
    assert "confianza" in motivos                       # parentesco con confianza 0.6
    assert all(json.loads(p.read_text())["estado"] == "en curso" for p in (tmp_path / "telar" / "personas").glob("*.json"))


# ═══════════════════════════ integración con Ollama real ═══════════════════════════


def _ollama_listo() -> bool:
    try:
        return LLMClient(CFG.con(timeout_s=5, reintentos=0)).disponible()
    except Exception:  # noqa: BLE001 - sin SDK o sin servidor
        return False


@pytest.mark.skipif(not _ollama_listo(), reason=f"Ollama no responde en {CFG.base_url} o falta el modelo {CFG.modelo}")
def test_pipeline_real_con_qwen3(tmp_path):
    informe = ejecutar_caso_real(tmp_path / "real.sqlite")
    assert informe["entidades"] >= 4, "Qwen 3 debería encontrar al menos alcalde, empresa, municipalidad y representante"
    nombres = {normalizar(n) for n in informe["nombres"]}
    assert any("sotomayor" in n for n in nombres) and any("austral ventisca" in n for n in nombres)
    assert informe["respuesta"]["citas"], "La respuesta del agente debe citar al menos una fuente"
    assert not informe["respuesta"]["verificacion"]["referencias_inventadas"]


def ejecutar_caso_real(ruta_db) -> dict:
    tiempos = {}
    t = time.monotonic()
    m = Motor(CFG, ruta_db=ruta_db)
    nombres, descartes, entidades, relaciones = set(), 0, 0, 0
    for d in CORPUS:
        t1 = time.monotonic()
        res = m.ingerir(d["id"], d["titulo"], d["texto"], url=d["url"], fecha=d["fecha"], tipo=d["tipo"], fuente=d["fuente"])
        tiempos[f"ingesta {d['id']}"] = time.monotonic() - t1
        nombres |= {e.nombre for e in res.entidades}
        descartes += len(res.descartes)
        print(f"  {d['id']}: {len(res.entidades)} entidades, {len(res.relaciones)} relaciones, "
              f"{len(res.descartes)} descartes, {res.segundos:.1f}s" + (f" · errores: {res.errores}" if res.errores else ""))
    m.almacen.agregar_registros(REGISTROS)
    est = m.almacen.estadisticas()
    entidades, relaciones = est["entidades"], est["relaciones"]
    t1 = time.monotonic()
    r = m.preguntar(PREGUNTA)
    tiempos["agente"] = time.monotonic() - t1
    tiempos["total"] = time.monotonic() - t
    print("\n" + r.texto_completo())
    for a in r.advertencias:
        print(f"  ⚠ {a}")
    print("\nTiempos (s): " + ", ".join(f"{k}={v:.1f}" for k, v in tiempos.items()))
    print(f"Almacén: {est}")
    m.cerrar()
    return {"entidades": entidades, "relaciones": relaciones, "descartes": descartes, "nombres": sorted(nombres),
            "respuesta": r.dict(), "tiempos": tiempos}


if __name__ == "__main__":
    import logging
    import tempfile

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not _ollama_listo():
        print(f"Ollama no responde en {CFG.base_url} o falta el modelo {CFG.modelo}.\n"
              f"  ollama serve &  ollama pull {CFG.modelo}  ollama pull {CFG.modelo_embeddings}")
        raise SystemExit(1)
    with tempfile.TemporaryDirectory() as d:
        print(f"Modelo: {CFG.modelo} · embeddings: {CFG.modelo_embeddings or '(desactivados)'}")
        ejecutar_caso_real(Path(d) / "demo.sqlite")
