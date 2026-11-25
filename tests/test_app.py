"""La app publicada en docs/: sin IA cerrada, independiente y con fuente en cada dato."""
import json
import re
from pathlib import Path

DOCS = Path(__file__).parents[1] / "docs"
TEXTOS = ["index.html", "telar-open.js", "sw.js", "manifest.webmanifest"]


def test_sin_ia_cerrada_ni_rutinas_remotas():
    malo = re.compile(r"claude|anthropic|gpt-?\d|groq|gemini|llama-?\d|fire_trigger|trig_", re.I)
    for n in TEXTOS:
        assert not malo.search((DOCS / n).read_text(encoding="utf-8")), n


def test_modelo_del_navegador_es_gemma_4():
    js = (DOCS / "telar-open.js").read_text(encoding="utf-8")
    assert re.findall(r"[\w.-]+-MLC", js) == ["gemma-4-E2B-it-q4f16_1-MLC", "gemma-4-E2B-it-q4f32_1-MLC"]


def test_no_depende_de_otra_app():
    for n in TEXTOS:
        assert "nereo" not in (DOCS / n).read_text(encoding="utf-8").lower(), n
    assert 'startsWith("telar-app-")' in (DOCS / "sw.js").read_text(encoding="utf-8")


def test_registro_con_fuente_y_sin_huerfanos():
    r = json.loads((DOCS / "data" / "registro.json").read_text(encoding="utf-8"))
    for k, v in r["vinculos"].items():
        assert v["personaId"] in r["personas"], k
        clase, ident = v["contraparte"].split(":", 1)
        assert ident in r["organizaciones" if clase == "org" else "personas"], k
        assert v["fuentes"] and all(s[1].startswith(("http://", "https://")) for s in v["fuentes"]), k
