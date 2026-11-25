/* Telar · análisis
   Cálculos puros sobre el registro ya citado. Aquí no hay modelos ni red: cada
   resultado se deriva de los vínculos y sus fuentes, y dice cuándo no se puede saber. */
(function (raiz, fabrica) {
  if (typeof module === "object" && module.exports) module.exports = fabrica();
  else raiz.TelarAnalisis = fabrica();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function norm(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }
  function dominio(u) {
    var h = "";
    try { h = new URL(u).hostname.toLowerCase().replace(/^www\./, ""); } catch (e) { return ""; }
    if (/(^|\.)wikipedia\.org$|(^|\.)wikidata\.org$/.test(h)) return "wikipedia.org";
    return h;
  }
  /* Dominio de quien publica: dos subdominios de la misma casa no se corroboran. */
  function casa(u) {
    var h = dominio(u); if (!h) return "";
    var p = h.split("."), n = p.length;
    var doble = n > 2 && /^(com|co|gob|gov|org|net|edu|ac)$/.test(p[n - 2]) && p[n - 1].length === 2;
    return p.slice(doble ? -3 : -2).join(".");
  }

  /* ── respaldo: qué sostiene una afirmación. Nunca dice «confirmado». */
  function respaldo(v) {
    var casas = {};
    (Array.isArray(v.fuentes) ? v.fuentes : []).forEach(function (f) {
      var c = Array.isArray(f) ? casa(f[1]) : "";
      if (c && c !== "wikipedia.org") casas[c] = 1;
    });
    var todas = (Array.isArray(v.fuentes) ? v.fuentes : []).filter(function (f) { return Array.isArray(f) && dominio(f[1]); }).length;
    var n = Object.keys(casas).length;
    if (!todas) return { id: "nada", nivel: 0, lb: "SIN FUENTE", casas: 0 };
    if (v.registro === "registro" || v.registro === "judicial")
      return { id: "oficial", nivel: 4, lb: v.registro === "judicial" ? "FUENTE JUDICIAL" : "REGISTRO OFICIAL", casas: n };
    if (n >= 2) return { id: "varias", nivel: 3, lb: n + " MEDIOS DISTINTOS", casas: n };
    if (!n) return { id: "terciaria", nivel: 1, lb: "SOLO WIKIPEDIA", casas: 0 };
    if (v.registro === "declaracion") return { id: "propia", nivel: 2, lb: "LO DECLARA LA FUENTE", casas: n };
    return { id: "una", nivel: 2, lb: "UNA SOLA FUENTE", casas: n };
  }
  function conRespaldo(v) { return respaldo(v).nivel >= 3; }

  /* ── fechas: «2014» es un intervalo, no un día */
  function rango(s) {
    var m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/.exec(String(s || "").trim());
    if (!m) return null;
    var y = +m[1], mes = m[2] ? +m[2] : null, d = m[3] ? +m[3] : null;
    if ((mes !== null && (mes < 1 || mes > 12)) || (d !== null && (d < 1 || d > 31))) return null;
    return [y * 10000 + (mes || 1) * 100 + (d || 1), y * 10000 + (mes || 12) * 100 + (d || 31)];
  }
  /* Dos vínculos con la misma organización: ¿hubo tiempo en común?
     «si» y «no» solo cuando las fechas lo obligan; lo demás es «no se sabe». */
  function solapan(a, b) {
    var ai = rango(a.desde), af = rango(a.hasta), bi = rango(b.desde), bf = rango(b.hasta);
    if ((ai && bf && ai[0] > bf[1]) || (bi && af && bi[0] > af[1])) return "no";
    if (ai && af && bi && bf && ai[1] <= bf[0] && bi[1] <= af[0]) return "si";
    return "no se sabe";
  }
  function tiempoComun(as, bs) {
    var r = [];
    as.forEach(function (a) { bs.forEach(function (b) { r.push({ a: a, b: b, r: solapan(a, b) }); }); });
    var si = r.filter(function (x) { return x.r === "si"; })[0];
    if (si) return { r: "si", desde: Math.max(+String(si.a.desde).slice(0, 4), +String(si.b.desde).slice(0, 4)),
                     hasta: Math.min(+String(si.a.hasta).slice(0, 4), +String(si.b.hasta).slice(0, 4)) };
    return { r: r.length && r.every(function (x) { return x.r === "no"; }) ? "no" : "no se sabe" };
  }

  /* ── grafo: aristas [{a, b, v}] con ids */
  function vecinos(aristas) {
    var m = new Map();
    function pon(x, y, e) { if (!m.has(x)) m.set(x, []); m.get(x).push({ a: y, e: e }); }
    aristas.forEach(function (e) { if (e.a !== e.b) { pon(e.a, e.b, e); pon(e.b, e.a, e); } });
    return m;
  }
  /* Caminos simples entre dos nodos, del más corto al más largo. */
  function caminos(aristas, de, a, maxSaltos, maxCaminos) {
    var g = vecinos(aristas), out = [], cola = [[{ n: de, e: null }]], pasos = 0;
    if (de === a || !g.has(de) || !g.has(a)) return out;
    while (cola.length && out.length < maxCaminos && pasos++ < 60000) {
      var c = cola.shift(), ult = c[c.length - 1].n;
      if (c.length - 1 >= maxSaltos) continue;
      var vistos = {}; // entre los mismos dos nodos, la arista mejor respaldada
      (g.get(ult) || []).forEach(function (x) {
        if (c.some(function (p) { return p.n === x.a; })) return;
        var previa = vistos[x.a];
        if (!previa || respaldo(x.e.v).nivel > respaldo(previa.v).nivel) vistos[x.a] = x.e;
      });
      Object.keys(vistos).forEach(function (n) {
        var nuevo = c.concat([{ n: n, e: vistos[n] }]);
        if (n === a) { if (out.length < maxCaminos) out.push(nuevo); }
        else cola.push(nuevo);
      });
    }
    return out;
  }
  function eslabonDebil(camino) {
    return camino.filter(function (p) { return p.e; }).map(function (p) { return respaldo(p.e.v); })
      .sort(function (x, y) { return x.nivel - y.nivel; })[0] || null;
  }
  /* Intermediación (Brandes): por cuántos caminos más cortos pasa cada nodo. */
  function puentes(aristas) {
    var g = new Map();
    aristas.forEach(function (e) {
      if (e.a === e.b) return;
      if (!g.has(e.a)) g.set(e.a, new Set()); if (!g.has(e.b)) g.set(e.b, new Set());
      g.get(e.a).add(e.b); g.get(e.b).add(e.a);
    });
    var C = {}; g.forEach(function (_, n) { C[n] = 0; });
    g.forEach(function (_, s) {
      var S = [], P = {}, sig = {}, d = {}, q = [s], del = {};
      g.forEach(function (_, n) { P[n] = []; sig[n] = 0; d[n] = -1; del[n] = 0; });
      sig[s] = 1; d[s] = 0;
      while (q.length) {
        var v = q.shift(); S.push(v);
        g.get(v).forEach(function (w) {
          if (d[w] < 0) { d[w] = d[v] + 1; q.push(w); }
          if (d[w] === d[v] + 1) { sig[w] += sig[v]; P[w].push(v); }
        });
      }
      while (S.length) {
        var w = S.pop();
        P[w].forEach(function (v) { del[v] += sig[v] / sig[w] * (1 + del[w]); });
        if (w !== s) C[w] += del[w];
      }
    });
    return Object.keys(C).map(function (n) { return { n: n, c: C[n] / 2 }; })
      .filter(function (x) { return x.c > 0; }).sort(function (x, y) { return y.c - x.c; });
  }

  /* ── personas: ¿podrían ser la misma, o confundirse? */
  function distancia(a, b) {
    var f = [], i, j;
    for (j = 0; j <= b.length; j++) f[j] = j;
    for (i = 1; i <= a.length; i++) { var ant = f[0]; f[0] = i;
      for (j = 1; j <= b.length; j++) { var t = f[j]; f[j] = Math.min(f[j] + 1, f[j - 1] + 1, ant + (a[i - 1] === b[j - 1] ? 0 : 1)); ant = t; } }
    return f[b.length];
  }
  function confundible(x, y) {
    var A = norm(x).split(" ").filter(Boolean), B = norm(y).split(" ").filter(Boolean);
    if (!A.length || !B.length) return false;
    var corto = A.length <= B.length ? A : B, largo = corto === A ? B : A;
    if (corto.length >= 2 && corto.every(function (t) { return largo.indexOf(t) >= 0; })) return true;
    return A.length >= 2 && B.length >= 2 && A.length === B.length && distancia(A.join(" "), B.join(" ")) <= 2;
  }
  function confundibles(nombre, lista) {
    return lista.filter(function (p) { return confundible(nombre, p.nombre) || (p.alias || []).some(function (a) { return confundible(nombre, a); }); });
  }
  function nombreCompleto(s) { return norm(s).split(" ").filter(function (t) { return t.length > 1; }).length >= 2; }

  /* ── nombres que podrían ser la misma organización. Solo sugiere. */
  var VACIAS = /^(de|del|la|las|los|el|y|e|sa|spa|ltda|limitada|s|a|chile|chilena|nacional|republica)$/;
  function piezas(s) { return norm(s).split(" ").filter(function (t) { return t && !VACIAS.test(t); }); }
  function posiblesDuplicados(lista) {
    var out = [];
    for (var i = 0; i < lista.length; i++) for (var j = i + 1; j < lista.length; j++) {
      var A = piezas(lista[i].nombre), B = piezas(lista[j].nombre);
      if (!A.length || !B.length) continue;
      var com = A.filter(function (t) { return B.indexOf(t) >= 0; }).length;
      if (norm(lista[i].nombre) === norm(lista[j].nombre) || com / new Set(A.concat(B)).size >= 0.75) out.push([lista[i], lista[j]]);
    }
    return out;
  }

  /* ── lo que falta verificar */
  /* Evidencia de un vínculo: qué partes sostienen sus frases y si siguen en la fuente. */
  var PARTES = { papel: 1, "organización": 1, inicio: 1, "término": 1, detalle: 1 };
  function evidencia(ev, k, v) {
    var ms = (ev && ev.vinculos && ev.vinculos[k] || []).filter(function (m) {
      return (v.fuentes || []).some(function (f) { return f && f[1] === m.url; }); });
    var cub = {}; ms.forEach(function (m) { if (m.vigente !== false) (m.cubre || []).forEach(function (c) { if (PARTES[c]) cub[c] = 1; }); });
    var falta = [];
    if (!cub.papel) falta.push("papel");
    if (v.desde && !cub.inicio) falta.push("inicio");
    if (v.hasta && !cub["término"]) falta.push("término");
    return { frases: ms, falta: ms.length ? falta : [], hay: ms.length > 0, perdida: ms.some(function (m) { return m.vigente === false; }) };
  }
  /* Enlace que abre la fuente en la frase, cuando el navegador lo admite. */
  function saltoA(m) {
    function c(s) { return encodeURIComponent(s).replace(/-/g, "%2D"); }
    var s = m.salto || [];
    return !s[0] ? m.url : m.url.split("#")[0] + "#:~:text=" + c(s[0]) + (s[1] ? "," + c(s[1]) : "");
  }
  function pendientes(reg, hoy, ev, ident) {
    var out = [], doc = {};
    if (ident && ident.personas) Object.keys(reg.personas).forEach(function (k) {
      var p = reg.personas[k], i = ident.personas[k], x = (ev && ev.identidad || {})[k] || {};
      function pon(clase, pide, u) { out.push({ clase: clase, persona: k, texto: p.nombre + (u ? " · " + dominio(u) : ""), pide: pide }); }
      if (!i && !p.identidad) return pon("SIN IDENTIDAD COMPROBADA", "Decir de qué nómina sale y qué la distingue de un homónimo.");
      if (i && !(i.ids || []).some(function (d) { return d.estado === "corroborado"; })) pon("SIN IDENTIFICADOR EXTERNO", "Buscar un identificador público que se pueda corroborar.");
      Object.keys(x).forEach(function (u) { if (!x[u].ok) pon("LA PÁGINA NO LA IDENTIFICA", "Comprobar que no hable de otra persona: " + x[u].motivo + ".", u); });
    });
    Object.keys(reg.vinculos).forEach(function (k) {
      var v = reg.vinculos[k], r = respaldo(v), p = reg.personas[v.personaId];
      var cp = String(v.contraparte || ""), id = cp.slice(cp.indexOf(":") + 1);
      var o = cp.indexOf("org:") === 0 ? reg.organizaciones[id] : reg.personas[id];
      var base = { vinculo: k, persona: v.personaId,
        texto: (p ? p.nombre : v.personaId) + " · " + (v.etiqueta || v.tipo) + (id ? " · " + (o ? o.nombre : id) : "") };
      function pon(clase, pide) { out.push(Object.assign({ clase: clase, pide: pide }, base)); }
      if (r.nivel === 0) pon("SIN FUENTE", "Encontrar una fuente pública o retirar el vínculo.");
      else if (r.id === "terciaria") pon("SOLO WIKIPEDIA", "Buscar la fuente primaria.");
      else if (r.id === "una") pon("UNA SOLA FUENTE", "Contrastar con un registro oficial o un segundo medio.");
      else if (r.id === "propia") pon("LO DECLARA LA FUENTE", "Contrastar con un registro independiente.");
      if (ev) {
        var e = evidencia(ev, k, v);
        if (!e.hay) pon("SIN FRASE LOCALIZADA", "Ubicar en la fuente la frase que lo sostiene.");
        if (e.perdida) pon("LA FRASE YA NO ESTÁ", "La página cambió: buscar una copia archivada o retirar el vínculo.");
        else if (e.falta.length) pon("DATO DEDUCIDO", "Ninguna frase da el " + e.falta.join(" ni el ") + ": buscar una que lo diga o dejarlo en blanco.");
      }
      if (!rango(v.desde) && !rango(v.hasta)) pon("SIN FECHAS", "Fechar el vínculo: sin fechas no se puede saber con quién coincidió.");
      var di = rango(v.desde), hf = rango(v.hasta);
      if (di && hf && di[0] > hf[1]) pon("FECHAS CRUZADAS", "El término es anterior al inicio: revisar la fuente.");
      if (hf && hoy && hf[1] < hoy && hf[1] > hoy - 10000) pon("TERMINÓ HACE POCO", "El plazo venció en el último año: verificar si continuó.");
      (v.fuentes || []).forEach(function (f) { if (f && dominio(f[1])) (doc[f[1]] = doc[f[1]] || []).push(k); });
    });
    Object.keys(doc).forEach(function (u) {
      var solo = doc[u].filter(function (k) { return reg.vinculos[k].fuentes.length === 1; });
      if (solo.length >= 5) out.push({ clase: "DOCUMENTO CRÍTICO", url: u, texto: dominio(u) + " sostiene solo " + solo.length + " afirmaciones",
        pide: "Guardar una copia y buscar un segundo respaldo." });
    });
    Object.keys(ev && ev.documentos || {}).forEach(function (u) {
      var d = ev.documentos[u];
      if (d.estado === "caida") out.push({ clase: "FUENTE CAÍDA", url: u, texto: dominio(u) + " ya no responde con esa página", pide: "Buscar una copia archivada." });
      if (d.estado === "sin respuesta") out.push({ clase: "FUENTE SIN COMPROBAR", url: u, texto: dominio(u) + " no dejó comprobarla el " + d.intento, pide: "Abrirla a mano." });
    });
    posiblesDuplicados(Object.keys(reg.organizaciones).map(function (k) { return { id: k, nombre: reg.organizaciones[k].nombre }; }))
      .forEach(function (d) { out.push({ clase: "¿MISMA ORGANIZACIÓN?", orgs: [d[0].id, d[1].id], texto: d[0].nombre + " / " + d[1].nombre,
        pide: "Decidir si son una sola. No se fusionan solas." }); });
    return out;
  }

  /* ── búsqueda en el texto de las afirmaciones */
  function buscar(reg, q, max) {
    var ts = norm(q).split(" ").filter(Boolean), out = [];
    if (!ts.length) return out;
    Object.keys(reg.vinculos).some(function (k) {
      var v = reg.vinculos[k];
      var t = " " + norm([v.etiqueta, v.detalle, v.desde, v.hasta, v.monto, (v.fuentes || []).map(function (f) { return f[0] + " " + dominio(f[1]); }).join(" ")].join(" "));
      if (ts.every(function (x) { return t.indexOf(" " + x) >= 0; })) out.push(k);
      return out.length >= max;
    });
    return out;
  }

  /* ── salidas para otras herramientas */
  function celda(s) {
    s = String(s === null || s === undefined ? "" : s);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g, '""') + '"';
  }
  function csv(filas) {
    var cab = ["persona", "tipo", "contraparte", "papel", "desde", "hasta", "monto", "moneda", "procedencia", "respaldo", "detalle", "fuentes"];
    return "﻿" + [cab].concat(filas).map(function (f) { return f.map(celda).join(","); }).join("\r\n") + "\r\n";
  }
  function xml(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function gexf(nodos, aristas) {
    return '<?xml version="1.0" encoding="UTF-8"?>\n<gexf xmlns="http://gexf.net/1.3" version="1.3">\n<graph defaultedgetype="directed" mode="static">\n' +
      '<attributes class="node"><attribute id="clase" title="clase" type="string"/></attributes>\n' +
      '<attributes class="edge"><attribute id="tipo" title="tipo" type="string"/><attribute id="respaldo" title="respaldo" type="string"/>' +
      '<attribute id="desde" title="desde" type="string"/><attribute id="hasta" title="hasta" type="string"/><attribute id="fuente" title="fuente" type="string"/></attributes>\n<nodes>\n' +
      nodos.map(function (n) { return '<node id="' + xml(n.id) + '" label="' + xml(n.label) + '"><attvalues><attvalue for="clase" value="' + xml(n.clase) + '"/></attvalues></node>'; }).join("\n") +
      "\n</nodes>\n<edges>\n" +
      aristas.map(function (e, i) {
        var v = e.v, a = { tipo: v.tipo, respaldo: respaldo(v).lb, desde: v.desde, hasta: v.hasta, fuente: (v.fuentes && v.fuentes[0] || [])[1] };
        return '<edge id="' + i + '" source="' + xml(e.a) + '" target="' + xml(e.b) + '" label="' + xml(v.etiqueta) + '"><attvalues>' +
          Object.keys(a).map(function (k) { return '<attvalue for="' + k + '" value="' + xml(a[k]) + '"/>'; }).join("") + "</attvalues></edge>";
      }).join("\n") + "\n</edges>\n</graph>\n</gexf>\n";
  }

  return { norm: norm, dominio: dominio, casa: casa, respaldo: respaldo, conRespaldo: conRespaldo, rango: rango, solapan: solapan,
    tiempoComun: tiempoComun, caminos: caminos, eslabonDebil: eslabonDebil, puentes: puentes, posiblesDuplicados: posiblesDuplicados,
    confundible: confundible, confundibles: confundibles, nombreCompleto: nombreCompleto, evidencia: evidencia, saltoA: saltoA, pendientes: pendientes, buscar: buscar, csv: csv, gexf: gexf };
});
