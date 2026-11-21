"""Almacén local: SQLite + FTS5 (léxico) + sqlite-vec (semántico) + grafo + registros.

Un solo archivo .sqlite, sin servidor. Si la extensión sqlite-vec no carga o no
hay modelo de embeddings, la búsqueda sigue funcionando solo con FTS5; el
resultado indica qué vías se usaron.

Búsqueda híbrida: se piden candidatos a FTS5 (BM25) y a sqlite-vec (coseno) y
se fusionan con Reciprocal Rank Fusion (k=60), que no requiere calibrar
puntajes de escalas distintas.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .ontologia import normalizar

log = logging.getLogger("telar_motor.almacen")

Embedder = Callable[[Sequence[str]], list[list[float]]]
RRF_K = 60

ESQUEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS documentos (
  id TEXT PRIMARY KEY, titulo TEXT NOT NULL, fuente TEXT, url TEXT, fecha TEXT,
  tipo TEXT, texto TEXT NOT NULL, agregado TEXT
);
CREATE TABLE IF NOT EXISTS fragmentos (
  id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
  orden INTEGER NOT NULL, inicio INTEGER NOT NULL, fin INTEGER NOT NULL, texto TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fragmentos_doc ON fragmentos(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS fragmentos_fts USING fts5(
  texto, content='fragmentos', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS fragmentos_ai AFTER INSERT ON fragmentos BEGIN
  INSERT INTO fragmentos_fts(rowid, texto) VALUES (new.id, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS fragmentos_ad AFTER DELETE ON fragmentos BEGIN
  INSERT INTO fragmentos_fts(fragmentos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
END;
CREATE TABLE IF NOT EXISTS entidades (
  id TEXT PRIMARY KEY, nombre TEXT NOT NULL, tipo TEXT NOT NULL,
  alias_json TEXT NOT NULL DEFAULT '[]', atributos_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS alias (
  clave TEXT NOT NULL, entidad_id TEXT NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
  PRIMARY KEY (clave, entidad_id)
);
CREATE TABLE IF NOT EXISTS relaciones (
  id INTEGER PRIMARY KEY, origen TEXT NOT NULL REFERENCES entidades(id),
  tipo TEXT NOT NULL, destino TEXT NOT NULL REFERENCES entidades(id),
  evidencia TEXT NOT NULL, confianza REAL NOT NULL,
  doc_id TEXT REFERENCES documentos(id) ON DELETE CASCADE,
  UNIQUE (origen, tipo, destino, doc_id)
);
CREATE INDEX IF NOT EXISTS relaciones_origen ON relaciones(origen);
CREATE INDEX IF NOT EXISTS relaciones_destino ON relaciones(destino);
CREATE TABLE IF NOT EXISTS registros (
  id TEXT PRIMARY KEY, tipo TEXT, fecha TEXT, organismo TEXT, proveedor TEXT,
  rut_proveedor TEXT, monto REAL, moneda TEXT, descripcion TEXT,
  doc_id TEXT REFERENCES documentos(id) ON DELETE SET NULL, url TEXT
);
"""

# columnas visibles para consultas estructuradas (lista blanca)
COLUMNAS: dict[str, tuple[str, ...]] = {
    "registros": ("id", "tipo", "fecha", "organismo", "proveedor", "rut_proveedor", "monto", "moneda", "descripcion", "doc_id", "url"),
    "entidades": ("id", "nombre", "tipo", "alias_json", "atributos_json"),
    "relaciones": ("id", "origen", "tipo", "destino", "evidencia", "confianza", "doc_id"),
    "documentos": ("id", "titulo", "fuente", "url", "fecha", "tipo"),
}
FUNCIONES_SQL = {
    "count", "sum", "avg", "min", "max", "total", "lower", "upper", "substr", "substring", "length", "trim",
    "coalesce", "ifnull", "round", "abs", "date", "strftime", "instr", "like", "replace", "cast", "group_concat",
    "printf", "iif", "nullif",
}
_STOP = set(
    "a al algo ante como con contra cual cuales de del desde donde el ella ellas ellos en entre era es esa ese eso "
    "esta este esto fue han hay la las le les lo los mas me mi muy no nos o para pero por que quien quienes se "
    "segun ser si sin sobre son su sus tambien te tiene un una uno unos y ya cuanto cuantos cuanta".split()
)


@dataclass
class Resultado:
    fragmento_id: int
    doc_id: str
    titulo: str
    url: str | None
    fecha: str | None
    fuente: str | None
    texto: str
    puntaje: float
    vias: list[str]

    def dict(self) -> dict[str, Any]:
        return asdict(self)


class ConsultaRechazada(ValueError):
    """Consulta estructurada fuera de lo permitido."""


class Almacen:
    def __init__(self, ruta: str | Path, embedder: Embedder | None = None, fragmento_chars: int = 900):
        self.ruta = str(ruta)
        self.embedder = embedder
        self.fragmento_chars = fragmento_chars
        if self.ruta != ":memory:":
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.ruta, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(ESQUEMA)
        self.vec_ok = self._cargar_vec(self.db)
        self.avisos: list[str] = []

    # ───────────────────────── vectorial ─────────────────────────

    @staticmethod
    def _cargar_vec(con: sqlite3.Connection) -> bool:
        try:
            import sqlite_vec

            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
            return True
        except Exception as e:  # noqa: BLE001 - extensión ausente o SQLite sin soporte
            log.warning("sqlite-vec no disponible (%s): búsqueda solo léxica", e)
            return False

    def _dims(self) -> int | None:
        fila = self.db.execute("SELECT valor FROM meta WHERE clave='dims'").fetchone()
        return int(fila[0]) if fila else None

    def _asegurar_vec(self, dims: int) -> bool:
        actual = self._dims()
        if actual is None:
            self.db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS fragmentos_vec USING vec0(embedding float[{int(dims)}] distance_metric=cosine)"
            )
            self.db.execute("INSERT INTO meta VALUES ('dims', ?)", (str(dims),))
            return True
        if actual != dims:
            self.avisos.append(f"El modelo de embeddings cambió de dimensión ({actual} → {dims}); no se indexa vectorialmente")
            return False
        return True

    def _embeber(self, textos: Sequence[str]) -> list[list[float]] | None:
        if not (self.vec_ok and self.embedder and textos):
            return None
        try:
            return self.embedder(list(textos))
        except Exception as e:  # noqa: BLE001
            aviso = f"Embeddings no disponibles ({type(e).__name__}): búsqueda solo léxica"
            if aviso not in self.avisos:
                self.avisos.append(aviso)
                log.warning(aviso)
            return None

    # ───────────────────────── ingesta ─────────────────────────

    def agregar_documento(
        self, id: str, titulo: str, texto: str, *, fuente: str = "", url: str = "", fecha: str = "", tipo: str = ""
    ) -> int:
        """Inserta o reemplaza un documento y lo indexa. Devuelve el número de fragmentos."""
        with self.db:
            if self.vec_ok and self._dims():  # primero los vectores: dependen de los ids de fragmento
                self.db.execute(
                    "DELETE FROM fragmentos_vec WHERE rowid IN (SELECT id FROM fragmentos WHERE doc_id=?)", (id,)
                )
            self.db.execute("DELETE FROM fragmentos WHERE doc_id=?", (id,))
            self.db.execute(
                "INSERT INTO documentos (id,titulo,fuente,url,fecha,tipo,texto,agregado) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET titulo=excluded.titulo, fuente=excluded.fuente, url=excluded.url, "
                "fecha=excluded.fecha, tipo=excluded.tipo, texto=excluded.texto, agregado=excluded.agregado",
                (id, titulo, fuente, url, fecha, tipo, texto, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            trozos = list(_trocear(texto, self.fragmento_chars))
            ids = []
            for orden, (ini, fin, t) in enumerate(trozos):
                cur = self.db.execute(
                    "INSERT INTO fragmentos (doc_id,orden,inicio,fin,texto) VALUES (?,?,?,?,?)", (id, orden, ini, fin, t)
                )
                ids.append(cur.lastrowid)
        vectores = self._embeber([f"{titulo}\n{t}" for _, _, t in trozos])
        if vectores and self._asegurar_vec(len(vectores[0])):
            import sqlite_vec

            with self.db:
                self.db.executemany(
                    "INSERT INTO fragmentos_vec(rowid, embedding) VALUES (?, ?)",
                    [(i, sqlite_vec.serialize_float32(v)) for i, v in zip(ids, vectores)],
                )
        return len(trozos)

    def guardar_extraccion(self, entidades: Iterable[Any], relaciones: Iterable[Any]) -> tuple[int, int]:
        """Persiste ``EntidadGlobal``/``RelacionGlobal`` fusionando alias y atributos."""
        ne = nr = 0
        with self.db:
            for e in entidades:
                fila = self.db.execute("SELECT alias_json, atributos_json FROM entidades WHERE id=?", (e.id,)).fetchone()
                alias = list(dict.fromkeys((json.loads(fila[0]) if fila else []) + list(e.alias)))
                atributos = {**(json.loads(fila[1]) if fila else {}), **e.atributos}
                self.db.execute(
                    "INSERT INTO entidades (id,nombre,tipo,alias_json,atributos_json) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET alias_json=excluded.alias_json, atributos_json=excluded.atributos_json",
                    (e.id, e.nombre, e.tipo, json.dumps(alias, ensure_ascii=False), json.dumps(atributos, ensure_ascii=False)),
                )
                for n in [e.nombre, *alias]:
                    self.db.execute("INSERT OR IGNORE INTO alias VALUES (?,?)", (normalizar(n), e.id))
                ne += 1
            for r in relaciones:
                cur = self.db.execute(
                    "INSERT INTO relaciones (origen,tipo,destino,evidencia,confianza,doc_id) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(origen,tipo,destino,doc_id) DO UPDATE SET evidencia=excluded.evidencia, "
                    "confianza=max(confianza, excluded.confianza)",
                    (r.origen, r.tipo, r.destino, r.evidencia, r.confianza, r.doc_id),
                )
                nr += cur.rowcount
        return ne, nr

    def agregar_registros(self, registros: Iterable[dict[str, Any]]) -> int:
        cols = COLUMNAS["registros"]
        n = 0
        with self.db:
            for r in registros:
                fila = [r.get(c) for c in cols]
                self.db.execute(
                    f"INSERT OR REPLACE INTO registros ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", fila
                )
                n += 1
        return n

    # ───────────────────────── búsqueda ─────────────────────────

    def buscar(self, consulta: str, filtros: dict[str, Any] | None = None, k: int = 6) -> tuple[list[Resultado], list[str]]:
        """Búsqueda híbrida. Devuelve (resultados, vías usadas)."""
        filtros = filtros or {}
        donde, params = _filtros_documento(filtros)
        candidatos = max(k * 4, 20)
        rangos: dict[int, dict[str, int]] = {}
        vias: list[str] = []

        expr = _expresion_fts(consulta)
        if expr:
            filas = self.db.execute(
                f"""SELECT f.id FROM fragmentos_fts JOIN fragmentos f ON f.id = fragmentos_fts.rowid
                    JOIN documentos d ON d.id = f.doc_id
                    WHERE fragmentos_fts MATCH ? {donde}
                    ORDER BY bm25(fragmentos_fts) LIMIT ?""",
                [expr, *params, candidatos],
            ).fetchall()
            for pos, f in enumerate(filas):
                rangos.setdefault(f[0], {})["fts"] = pos
            vias.append("fts5")

        if self.vec_ok and self._dims():
            vq = self._embeber([consulta])
            if vq:
                import sqlite_vec

                filas = self.db.execute(
                    f"""SELECT v.rowid FROM (
                          SELECT rowid, distance FROM fragmentos_vec WHERE embedding MATCH ? AND k = ?
                        ) v JOIN fragmentos f ON f.id = v.rowid JOIN documentos d ON d.id = f.doc_id
                        WHERE 1=1 {donde} ORDER BY v.distance""",
                    [sqlite_vec.serialize_float32(vq[0]), candidatos * 2, *params],
                ).fetchall()
                for pos, f in enumerate(filas[:candidatos]):
                    rangos.setdefault(f[0], {})["vec"] = pos
                vias.append("sqlite-vec")

        puntajes = {fid: sum(1.0 / (RRF_K + p + 1) for p in r.values()) for fid, r in rangos.items()}
        mejores = sorted(puntajes, key=puntajes.get, reverse=True)[:k]
        if not mejores:
            return [], vias
        filas = {
            f["id"]: f
            for f in self.db.execute(
                f"""SELECT f.id, f.doc_id, f.texto, d.titulo, d.url, d.fecha, d.fuente FROM fragmentos f
                    JOIN documentos d ON d.id = f.doc_id WHERE f.id IN ({','.join('?' * len(mejores))})""",
                mejores,
            )
        }
        return [
            Resultado(
                fid, filas[fid]["doc_id"], filas[fid]["titulo"], filas[fid]["url"] or None, filas[fid]["fecha"] or None,
                filas[fid]["fuente"] or None, filas[fid]["texto"], round(puntajes[fid], 5), sorted(rangos[fid]),
            )
            for fid in mejores
        ], vias

    # ───────────────────────── grafo ─────────────────────────

    def resolver_entidad(self, nombre: str) -> list[sqlite3.Row]:
        clave = normalizar(nombre)
        filas = self.db.execute(
            "SELECT DISTINCT e.* FROM alias a JOIN entidades e ON e.id = a.entidad_id WHERE a.clave = ?", (clave,)
        ).fetchall()
        if filas:
            return filas
        patron = "%" + clave.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return self.db.execute(
            "SELECT DISTINCT e.* FROM alias a JOIN entidades e ON e.id = a.entidad_id "
            "WHERE a.clave LIKE ? ESCAPE '\\' LIMIT 5",
            (patron,),
        ).fetchall()

    def conexiones(self, entidad_id: str, profundidad: int = 1, limite: int = 60) -> list[dict[str, Any]]:
        profundidad = max(1, min(int(profundidad), 3))
        filas = self.db.execute(
            """
            WITH RECURSIVE
              aristas(a, b, rid) AS (
                SELECT origen, destino, id FROM relaciones
                UNION ALL SELECT destino, origen, id FROM relaciones
              ),
              camino(nodo, nivel, visitados, rid) AS (
                SELECT ?, 0, ',' || ? || ',', NULL
                UNION ALL
                SELECT ar.b, c.nivel + 1, c.visitados || ar.b || ',', ar.rid
                FROM camino c JOIN aristas ar ON ar.a = c.nodo
                WHERE c.nivel < ? AND instr(c.visitados, ',' || ar.b || ',') = 0
                LIMIT 5000
              )
            SELECT r.id, r.tipo, r.evidencia, r.confianza, r.doc_id, MIN(c.nivel) AS nivel,
                   eo.id AS origen_id, eo.nombre AS origen, eo.tipo AS origen_tipo,
                   ed.id AS destino_id, ed.nombre AS destino, ed.tipo AS destino_tipo,
                   d.titulo, d.url, d.fecha
            FROM camino c JOIN relaciones r ON r.id = c.rid
            JOIN entidades eo ON eo.id = r.origen JOIN entidades ed ON ed.id = r.destino
            LEFT JOIN documentos d ON d.id = r.doc_id
            GROUP BY r.id ORDER BY nivel, r.confianza DESC LIMIT ?
            """,
            (entidad_id, entidad_id, profundidad, limite),
        ).fetchall()
        return [dict(f) for f in filas]

    # ───────────────────────── registros estructurados ─────────────────────────

    def consultar(self, sql_o_filtro: str | dict[str, Any], limite: int = 50) -> tuple[list[str], list[dict[str, Any]]]:
        """Consulta de solo lectura. Acepta un filtro (dict) o un SELECT acotado.

        * Filtro: ``{"tabla": "registros", "donde": [{"campo": "monto", "op": ">=", "valor": 1e8}],
          "orden": "-monto", "limite": 20, "campos": ["proveedor", "monto"]}``
        * SQL: un único SELECT sobre tablas de la lista blanca. Se ejecuta con
          un autorizador que solo permite lecturas, en una conexión de solo
          lectura y con tope de tiempo y de filas.
        """
        limite = max(1, min(int(limite), 200))
        if isinstance(sql_o_filtro, dict):
            sql, params = _compilar_filtro(sql_o_filtro, limite)
            cur = self.db.execute(sql, params)
        else:
            sql = _validar_sql(str(sql_o_filtro))
            cur = self._lectura().execute(f"SELECT * FROM ({sql}) LIMIT {limite}")
        columnas = [c[0] for c in cur.description]
        return columnas, [dict(zip(columnas, f)) for f in cur.fetchall()]

    def _lectura(self) -> sqlite3.Connection:
        if getattr(self, "_ro", None) is None:
            if self.ruta == ":memory:":
                con = self.db  # pruebas en memoria: misma conexión, mismo autorizador
            else:
                con = sqlite3.connect(f"file:{self.ruta}?mode=ro", uri=True, check_same_thread=False)
            self._ro = con
        con = self._ro
        con.set_authorizer(_autorizador)
        inicio = time.monotonic()
        con.set_progress_handler(lambda: 1 if time.monotonic() - inicio > 3 else 0, 10_000)
        return _ConexionAcotada(con, self.db is con)

    def documento(self, doc_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()

    def estadisticas(self) -> dict[str, Any]:
        q = lambda t: self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
        return {
            "documentos": q("documentos"), "fragmentos": q("fragmentos"), "entidades": q("entidades"),
            "relaciones": q("relaciones"), "registros": q("registros"),
            "vectorial": bool(self.vec_ok and self._dims()), "dims": self._dims(),
        }

    def cerrar(self) -> None:
        if getattr(self, "_ro", None) is not None and self._ro is not self.db:
            self._ro.close()
        self.db.close()


class _ConexionAcotada:
    """Ejecuta con autorizador y lo retira después si la conexión es compartida."""

    def __init__(self, con: sqlite3.Connection, compartida: bool):
        self.con, self.compartida = con, compartida

    def execute(self, sql: str, params: Sequence[Any] = ()):
        try:
            cur = self.con.execute(sql, params)
            filas = cur.fetchall()
            descripcion = cur.description
        except sqlite3.DatabaseError as e:
            raise ConsultaRechazada(f"Consulta rechazada o inválida: {e}") from e
        finally:
            if self.compartida:
                self.con.set_authorizer(None)
                self.con.set_progress_handler(None, 0)
        return _Cursor(descripcion, filas)


class _Cursor:
    def __init__(self, description, filas):
        self.description, self._filas = description, filas

    def fetchall(self):
        return self._filas


def _autorizador(accion: int, a1, a2, db_nombre, origen) -> int:
    if accion in (sqlite3.SQLITE_SELECT, getattr(sqlite3, "SQLITE_RECURSIVE", 33)):
        return sqlite3.SQLITE_OK
    if accion == sqlite3.SQLITE_READ:
        if a1 in COLUMNAS and (not a2 or a2 in COLUMNAS[a1]):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if accion == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (a2 or "").lower() in FUNCIONES_SQL else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


_SQL_PROHIBIDO = re.compile(r"\b(attach|detach|pragma|insert|update|delete|drop|alter|create|replace\s+into|vacuum|load_extension)\b", re.I)


def _validar_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ConsultaRechazada("Solo se admite una sentencia")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise ConsultaRechazada("Solo se admiten consultas SELECT")
    if _SQL_PROHIBIDO.search(s):
        raise ConsultaRechazada("La consulta contiene operaciones no permitidas")
    return s


_OPS = {"=": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


def _compilar_filtro(f: dict[str, Any], limite: int) -> tuple[str, list[Any]]:
    tabla = f.get("tabla", "registros")
    if tabla not in COLUMNAS:
        raise ConsultaRechazada(f"Tabla no permitida: {tabla}. Opciones: {sorted(COLUMNAS)}")
    cols = COLUMNAS[tabla]
    campos = f.get("campos") or list(cols)
    for c in campos:
        if c not in cols:
            raise ConsultaRechazada(f"Campo no permitido en {tabla}: {c}")
    condiciones, params = [], []
    for cond in f.get("donde") or []:
        campo, op, valor = cond.get("campo"), str(cond.get("op", "=")).lower(), cond.get("valor")
        if campo not in cols:
            raise ConsultaRechazada(f"Campo no permitido en {tabla}: {campo}")
        if op in _OPS:
            condiciones.append(f"{campo} {_OPS[op]} ?")
            params.append(valor)
        elif op == "contiene":
            condiciones.append(f"lower({campo}) LIKE ? ESCAPE '\\'")
            v = str(valor).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{v}%")
        elif op == "entre" and isinstance(valor, list) and len(valor) == 2:
            condiciones.append(f"{campo} BETWEEN ? AND ?")
            params.extend(valor)
        elif op == "en" and isinstance(valor, list) and valor:
            condiciones.append(f"{campo} IN ({','.join('?' * len(valor))})")
            params.extend(valor)
        else:
            raise ConsultaRechazada(f"Operador no permitido: {op}. Opciones: =, !=, >, >=, <, <=, contiene, entre, en")
    sql = f"SELECT {', '.join(campos)} FROM {tabla}"
    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)
    orden = f.get("orden")
    if orden:
        desc = orden.startswith("-")
        campo = orden.lstrip("-+")
        if campo not in cols:
            raise ConsultaRechazada(f"No se puede ordenar por {campo}")
        sql += f" ORDER BY {campo} {'DESC' if desc else 'ASC'}"
    sql += f" LIMIT {min(int(f.get('limite', limite)), limite)}"
    return sql, params


def _filtros_documento(filtros: dict[str, Any]) -> tuple[str, list[Any]]:
    partes, params = [], []
    if filtros.get("tipo"):
        partes.append("AND d.tipo = ?")
        params.append(filtros["tipo"])
    if filtros.get("fuente"):
        partes.append("AND lower(d.fuente) LIKE ?")
        params.append(f"%{str(filtros['fuente']).lower()}%")
    if filtros.get("fecha_desde"):
        partes.append("AND d.fecha >= ?")
        params.append(filtros["fecha_desde"])
    if filtros.get("fecha_hasta"):
        partes.append("AND d.fecha <= ?")
        params.append(filtros["fecha_hasta"])
    if filtros.get("doc_ids"):
        ids = list(filtros["doc_ids"])
        partes.append(f"AND d.id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    return " ".join(partes), params


def _expresion_fts(consulta: str) -> str:
    """Convierte texto libre en una expresión FTS5 segura (términos entre comillas, OR)."""
    terminos = [t for t in re.findall(r"\w+", normalizar(consulta)) if len(t) > 1 and t not in _STOP]
    terminos = list(dict.fromkeys(terminos))[:16]
    return " OR ".join(f'"{t}"' for t in terminos)


def _trocear(texto: str, maximo: int) -> Iterable[tuple[int, int, str]]:
    """Fragmentos de ~``maximo`` caracteres cortados en límites de oración, con su posición."""
    oraciones = [(m.start(), m.end()) for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|\n|$)", texto)]
    ini = fin = None
    for a, b in oraciones:
        if not texto[a:b].strip():
            continue
        if ini is None:
            ini, fin = a, b
        elif b - ini > maximo:
            yield ini, fin, texto[ini:fin].strip()
            ini, fin = a, b
        else:
            fin = b
    if ini is not None:
        yield ini, fin, texto[ini:fin].strip()
