# Telar

Registro de influencia de figuras públicas, con la fuente de cada afirmación.

**App:** https://simonrovi-web.github.io/telar/

- `docs/` es la app que publica GitHub Pages. Abre en modo lectura; `?taller` permite editar en el propio navegador, con Gemma 4 (Apache 2.0) para generar pistas sin verificar.
- `docs/data/registro.json` es el registro publicado.
- `telar_motor/` es el motor de investigación documental, descrito abajo.

Toda la IA de Telar es de pesos abiertos con licencia libre.

## Telar · motor

Motor de investigación documental en español que corre en tu propio equipo. Lee decretos, contratos, notas de prensa e informes; extrae personas, organizaciones, lugares, fechas, documentos y montos con sus relaciones; los guarda en un archivo SQLite y responde preguntas con un agente que **cita la fuente exacta de cada afirmación**.

Usa **Qwen 3** a través de **Ollama**, con el SDK de OpenAI. No envía documentos a ningún servicio externo.

```
documento ─▶ extractor ─▶ verificación ─▶ almacén SQLite ◀── agente ◀── pregunta
             Qwen 3 +     · la entidad     · FTS5 (palabras)     │ search_documents
             esquema      aparece          · sqlite-vec          │ get_entity_connections
             Pydantic     · la cita es       (significado)       │ query_structured_records
                          literal          · grafo de relaciones ▼
                          · tipos          · registros       respuesta con [F#] verificados
                          compatibles                        + fuentes armadas por el código
                                              exportar ─▶ borradores JSON para el visor Telar
```

## Empezar en cinco minutos

Requisitos: Python 3.10 o superior y [Ollama](https://ollama.com).

```bash
git clone https://github.com/simonrovi-web/telar.git && cd telar
pip install -e .
ollama pull qwen3:4b && ollama pull bge-m3
cp .env.example .env            # cambia OLLAMA_MODEL a qwen3:4b, o deja qwen3:14b si tienes GPU

telar demo                      # caso ficticio completo: ingesta, extracción y una pregunta
```

Con tus propios documentos:

```bash
telar ingerir decreto.txt --titulo "Decreto 2.214" --tipo decreto --fecha 2025-03-14 --url https://…
telar registros contratos.json          # lista de objetos: id, fecha, organismo, proveedor, monto…
telar preguntar "¿Quién firmó la adjudicación y por qué monto?"
telar exportar exportacion/telar        # borradores para revisar antes de cargarlos en Telar
telar estado                            # ¿responde Ollama? ¿qué hay en el almacén?
```

## Qué modelo usar

| Equipo | `OLLAMA_MODEL` |
|---|---|
| CPU (GitHub Actions, portátil sin GPU) | `qwen3:4b` |
| 16 GB de RAM o GPU de 8 GB | `qwen3:8b` |
| GPU de 12–16 GB | `qwen3:14b` (valor por defecto) |
| GPU de 24 GB o más | `qwen3:32b` o `qwen3:30b-a3b` |

Todas las opciones están en [`.env.example`](.env.example).

## Por qué confiar en lo que devuelve

El modelo propone y el código verifica. Nada de lo que sigue depende del modelo:

1. **Entidades.** Solo se guardan las que aparecen en el texto, por su nombre o por un alias.
2. **Relaciones.** Cada una necesita una cita literal del documento. Se toleran diferencias de comillas y espacios, no paráfrasis. Una relación con tipos incompatibles se descarta: una empresa no "firma" un decreto, una organización no es "familiar de" nadie.
3. **Lo descartado queda registrado** con su motivo, para revisión.
4. **Respuestas del agente.** Se eliminan las referencias [F#] que ninguna herramienta entregó. Se comprueba que las citas entre «» estén en la fuente y se cuentan las frases sin respaldo. La lista de fuentes la arma el código.
5. **Consultas estructuradas.** Son de solo lectura, con lista blanca de tablas y columnas, una sola sentencia y tope de tiempo y de filas.

## Detalles técnicos de Ollama

- El razonamiento de Qwen 3 se apaga con `reasoning_effort: "none"`. En la API compatible con OpenAI, Ollama ignora `think: false` ([documentación](https://docs.ollama.com/api/openai-compatibility), [issue #17969](https://github.com/ollama/ollama/issues/17969)). La extracción va sin razonamiento: más rápida y determinista.
- El razonamiento llega en `message.reasoning`, aparte del texto, y sus tokens cuentan contra `max_tokens`. Si una respuesta se corta sin contenido, el cliente reintenta con el doble de margen.
- `tool_choice` no está soportado, así que nunca se envía. La obligación de buscar antes de responder la imponen el prompt y un recordatorio.
- Si tu versión de Ollama rechaza `json_schema` o `reasoning_effort`, el cliente se adapta solo. Todo JSON se valida con Pydantic y, si falla, se pide una corrección mostrándole los errores al modelo.

## Estructura

| Archivo | Responsabilidad |
|---|---|
| `telar_motor/config.py` | Variables `OLLAMA_*` y `MOTOR_*`. |
| `telar_motor/llm_client.py` | Reintentos, tiempos de espera, temperaturas, razonamiento, salida estructurada. |
| `telar_motor/ontologia.py` | Tipos de entidad, vocabulario cerrado de relaciones, modelos Pydantic. |
| `telar_motor/entity_extractor.py` | Extracción por fragmentos, fusión de alias, verificación. |
| `telar_motor/almacen.py` | SQLite: búsqueda híbrida (RRF), grafo hasta profundidad 3, registros. |
| `telar_motor/herramientas.py` | Las tres herramientas del agente y el registro de evidencia. |
| `telar_motor/search_agent.py` | Bucle del agente y verificación de citas. |
| `telar_motor/telar_export.py` | Personas, organizaciones y vínculos en el formato del visor Telar. |
| `ejemplos/puerto_ventisca/` | Caso de prueba **ficticio**: municipio, autoridades, empresas y documentos inventados. |
| `tests/test_pipeline.py` | 18 pruebas sin modelo y una prueba con Qwen 3 real que corre si Ollama responde. |

## Pruebas

```bash
pip install -e ".[dev]"
python -m pytest -q                              # sin modelo; el caso real corre si Ollama responde
python -m pytest tests/test_pipeline.py -s -k real   # solo el caso real, con tiempos
```

En GitHub Actions, **Pruebas** corre en cada push. **Qwen 3 real** instala Ollama, descarga el modelo y ejecuta el caso ficticio completo. Se lanza a mano desde la pestaña Actions, y ahí se elige el modelo.

## Alcance

Es una herramienta para trabajo periodístico sobre **documentos públicos y figuras públicas**. Registra lo que los documentos dicen, no infiere intenciones ni delitos. Todo lo que exporta queda como borrador para revisión humana.
