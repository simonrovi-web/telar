// node --test tests/analisis.test.js
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const A = require("../docs/analisis.js");
const REG = JSON.parse(fs.readFileSync(path.join(__dirname, "../docs/data/registro.json"), "utf8"));
const f = (...us) => us.map(u => ["x", u]);

test("el respaldo nunca dice confirmado y cuenta casas, no enlaces", () => {
  assert.equal(A.respaldo({ registro: "prensa", fuentes: [] }).id, "nada");
  assert.equal(A.respaldo({ registro: "prensa", fuentes: f("https://www.emol.com/a", "https://tv.emol.com/b") }).id, "una");
  assert.equal(A.respaldo({ registro: "prensa", fuentes: f("https://emol.com/a", "https://ciperchile.cl/b") }).id, "varias");
  assert.equal(A.respaldo({ registro: "prensa", fuentes: f("https://es.wikipedia.org/a", "https://en.wikipedia.org/b") }).id, "terciaria");
  assert.equal(A.respaldo({ registro: "prensa", fuentes: f("https://es.wikipedia.org/a", "https://emol.com/b") }).id, "una");
  assert.equal(A.respaldo({ registro: "declaracion", fuentes: f("https://x.cl") }).id, "propia");
  assert.equal(A.respaldo({ registro: "registro", fuentes: f("https://bcn.cl") }).id, "oficial");
  assert.equal(A.respaldo({ registro: "registro", fuentes: [["x", "javascript:alert(1)"]] }).id, "nada");
  for (const v of Object.values(REG.vinculos)) assert.ok(!/confirmad|verificad/i.test(A.respaldo(v).lb));
});

test("casas con dominio doble", () => {
  assert.equal(A.casa("https://a.b.gob.cl/x"), "b.gob.cl");
  assert.equal(A.casa("https://www.lanacion.com.ar/x"), "lanacion.com.ar");
  assert.notEqual(A.casa("https://a.gob.cl"), A.casa("https://b.gob.cl"));
});

test("el tiempo en común solo se afirma cuando las fechas obligan", () => {
  const s = (a, b, c, d) => A.solapan({ desde: a, hasta: b }, { desde: c, hasta: d });
  assert.equal(s("2010", "2014", "2012", "2016"), "si");
  assert.equal(s("2010", "2014", "2015", "2016"), "no");
  assert.equal(s("2010", "2014", "2014", "2016"), "no se sabe"); // mismo año límite
  assert.equal(s("2010-01-01", "2014-03-10", "2014-03-11", "2016"), "no");
  assert.equal(s("2010", "", "2012", "2016"), "no se sabe");      // sin término no se supone vigente
  assert.equal(s("", "", "", ""), "no se sabe");
  assert.equal(s("", "2009", "2010", ""), "no");
  assert.equal(A.rango("2014-19"), null);
  assert.deepEqual(A.tiempoComun([{ desde: "2000", hasta: "2001" }, { desde: "2010", hasta: "2014" }], [{ desde: "2011", hasta: "2020" }]),
    { r: "si", desde: 2011, hasta: 2014 });
  assert.equal(A.tiempoComun([{ desde: "2000", hasta: "2001" }], [{ desde: "2011", hasta: "2020" }]).r, "no");
});

const e = (a, b, reg = "prensa") => ({ a, b, v: { registro: reg, fuentes: f("https://emol.com/x") } });
test("caminos simples, del más corto al más largo, con su eslabón débil", () => {
  const g = [e("a", "x", "registro"), e("x", "b", "registro"), e("a", "y"), e("y", "z"), e("z", "b"), e("a", "a")];
  const c = A.caminos(g, "a", "b", 5, 5);
  assert.deepEqual(c.map(p => p.map(q => q.n).join("")), ["axb", "ayzb"]);
  assert.equal(A.eslabonDebil(c[0]).id, "oficial");
  assert.equal(A.eslabonDebil(c[1]).id, "una");
  assert.deepEqual(A.caminos(g, "a", "a", 5, 5), []);
  assert.deepEqual(A.caminos(g, "a", "nadie", 5, 5), []);
  assert.equal(A.caminos(g, "a", "b", 2, 5).length, 1);
});

test("entre dos nodos se usa la arista mejor respaldada", () => {
  const c = A.caminos([e("a", "b"), e("a", "b", "judicial")], "a", "b", 3, 3);
  assert.equal(c.length, 1);
  assert.equal(A.eslabonDebil(c[0]).id, "oficial");
});

test("puentes: intermediación de una estrella y una cadena", () => {
  const p = A.puentes([e("a", "c"), e("b", "c"), e("d", "c"), e("a", "c")]);
  assert.deepEqual(p, [{ n: "c", c: 3 }]);
  assert.deepEqual(A.puentes([e("a", "b"), e("b", "c"), e("c", "d")]).map(x => [x.n, x.c]), [["b", 2], ["c", 2]]);
});

test("duplicados: sugiere sin juntar lo distinto", () => {
  const d = (...ns) => A.posiblesDuplicados(ns.map((nombre, id) => ({ id, nombre }))).length;
  assert.equal(d("Bancard", "Bancard S.A."), 1);
  assert.equal(d("Banco de Talca", "Banco de Chile"), 0);
  assert.equal(d("Senado de Chile", "Senado de la República de Chile"), 1);
  assert.equal(d("Universidad Católica", "Universidad de Chile"), 0);
});

test("pendientes sobre casos construidos", () => {
  const reg = { personas: { p: { nombre: "P" } }, organizaciones: {}, vinculos: {
    a: { personaId: "p", tipo: "cargo", registro: "prensa", desde: "2015", hasta: "2010", fuentes: f("https://x.cl") },
    b: { personaId: "p", tipo: "cargo", registro: "registro", desde: "2020", hasta: "2026-06-01", fuentes: f("https://x.gob.cl") } } };
  const cl = A.pendientes(reg, 20261125).map(x => x.vinculo + ":" + x.clase);
  assert.ok(cl.includes("a:UNA SOLA FUENTE") && cl.includes("a:FECHAS CRUZADAS") && cl.includes("b:TERMINÓ HACE POCO"));
  assert.ok(!cl.some(x => x.startsWith("b:UNA")));
});

test("búsqueda en afirmaciones y salidas seguras", () => {
  assert.ok(A.buscar(REG, "subsecretaria", 10).length >= 1);
  assert.deepEqual(A.buscar(REG, "", 10), []);
  const c = A.csv([["=HYPERLINK(1)", 'con "comillas"']]);
  assert.ok(c.includes("\"'=HYPERLINK(1)\"") && c.includes('""comillas""'));
  const g = A.gexf([{ id: 'a"<', label: "A & B", clase: "p" }], [e("a", "b")]);
  assert.ok(g.includes("A &amp; B") && g.includes("a&quot;&lt;"));
});

test("evidencia: lo deducido y lo perdido quedan a la vista", () => {
  const v = { desde: "2005", hasta: "", fuentes: f("https://a.cl/x"), registro: "registro" };
  const m = (o) => ({ url: "https://a.cl/x", cubre: ["papel"], salto: ["uno dos", "tres"], ...o });
  const E = ms => A.evidencia({ vinculos: { k: ms } }, "k", v);
  assert.equal(E([]).hay, false);
  assert.deepEqual(E([m()]).falta, ["inicio"]);
  assert.deepEqual(E([m({ cubre: ["papel", "inicio"] })]).falta, []);
  assert.equal(E([m({ vigente: false })]).perdida, true);
  assert.equal(E([m({ url: "https://otra.cl" })]).hay, false);
  assert.equal(A.saltoA(m({ salto: ["a-b c", "d"] })), "https://a.cl/x#:~:text=a%2Db%20c,d");
  const reg = { personas: {}, organizaciones: {}, vinculos: { k: { ...v, personaId: "p", tipo: "cargo" } } };
  assert.ok(A.pendientes(reg, 20261125, { vinculos: { k: [m()] } }).some(x => x.clase === "DATO DEDUCIDO"));
  assert.ok(A.pendientes(reg, 20261125, { vinculos: {} }).some(x => x.clase === "SIN FRASE LOCALIZADA"));
  assert.ok(!A.pendientes(reg, 20261125).some(x => /FRASE|DEDUCIDO/.test(x.clase)));
});
