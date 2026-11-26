/* Telar · investigaciones dentro de la app.

   Pide una investigación, sigue su avance y lee el borrador sin salir de Telar.
   El investigador vive en un repositorio privado de GitHub: para hablar con él, quien
   administra pega una vez una llave de acceso (token) que queda solo en este navegador.
   Sin esa llave, la app sigue siendo de lectura y nada cambia para quien visita.

   La app le pasa sus funciones con TelarInvestigador.iniciar({...}) al arrancar. */
(function () {
  "use strict";

  var el, clr, $, toast, openPanel, togglePanel, openSheet, lab, inp, V, railBtn, modoPanel;
  function iniciar(h) {
    el = h.el; clr = h.clr; $ = h.$; toast = h.toast; openPanel = h.openPanel; togglePanel = h.togglePanel;
    openSheet = h.openSheet; lab = h.lab; inp = h.inp; V = h.V; railBtn = h.railBtn; modoPanel = h.modoPanel;
  }

  var REPO = "simonrovi-web/telar-investigador";
  var API = "https://api.github.com";
  var CLAVE = "telar.investigador.token";
  var MARCA = "### Nombre y apellido";
  var CREAR_TOKEN = "https://github.com/settings/personal-access-tokens/new" +
    "?name=Telar%20app&description=Pedir%20investigaciones%20desde%20Telar" +
    "&target_name=simonrovi-web&expires_in=90&issues=write&contents=read";

  var cache = {};          // número de issue cerrado → {estado, texto, html}
  var lista = null;        // última lista leída
  var reloj = null;
  var usuario = null;

  /* ── la llave, solo en este navegador ─────────────────────────────── */
  function token() { try { return localStorage.getItem(CLAVE) || ""; } catch (e) { return ""; } }
  function guardarToken(t) { try { localStorage.setItem(CLAVE, t); return true; } catch (e) { return false; } }
  function borrarToken() { try { localStorage.removeItem(CLAVE); } catch (e) {} usuario = null; lista = null; cache = {}; }

  function gh(ruta, opciones) {
    opciones = opciones || {};
    var h = { "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
              "Authorization": "Bearer " + (opciones.token || token()) };
    if (opciones.cuerpo) h["Content-Type"] = "application/json";
    return fetch(API + ruta, { method: opciones.metodo || "GET", headers: h, cache: "no-store",
                               body: opciones.cuerpo ? JSON.stringify(opciones.cuerpo) : undefined })
      .then(function (r) {
        if (r.status === 401) throw new Error("La llave no sirve o venció. Desconecta y pega una nueva.");
        if (r.status === 403 || r.status === 404)
          throw new Error("La llave no tiene permiso sobre el investigador. Revisa que incluya el repositorio telar-investigador con Issues de lectura y escritura.");
        if (!r.ok) throw new Error("GitHub respondió " + r.status + ". Prueba de nuevo en un momento.");
        return opciones.texto ? r.text() : r.json();
      }, function () { throw new Error("Sin conexión con GitHub."); });
  }

  /* ── lectura de los pedidos ────────────────────────────────────────── */
  function campo(cuerpo, titulo) {
    var m = new RegExp("###\\s*" + titulo + "[^\\n]*\\n+([\\s\\S]*?)(?=\\n###|$)").exec(cuerpo || "");
    var v = m ? m[1].trim().split("\n")[0].trim() : "";
    return v === "_No response_" ? "" : v;
  }
  function minutos(desde, hasta) {
    return Math.max(0, Math.round(((hasta ? new Date(hasta) : new Date()) - new Date(desde)) / 60000));
  }
  function haceCuanto(fecha) {
    var m = minutos(fecha);
    if (m < 1) return "recién";
    if (m < 60) return "hace " + m + " min";
    var h = Math.round(m / 60);
    if (h < 48) return "hace " + h + " h";
    return new Date(fecha).toLocaleDateString("es-CL", { day: "numeric", month: "short" });
  }
  function clasificar(texto) {
    var t = texto || "";
    if (/Se detuvo:/.test(t)) {
      var m = /Se detuvo:\s*([^*\n]+)/.exec(t);
      var mo = m ? m[1].trim() : "";
      return { estado: "detenida", motivo: mo.charAt(0).toUpperCase() + mo.slice(1) };
    }
    if (/La investigación falló/.test(t)) return { estado: "fallida", motivo: "No produjo nada. Puedes pedirla de nuevo." };
    return { estado: "lista", motivo: "" };
  }

  function leerPedidos() {
    return gh("/repos/" + REPO + "/issues?state=all&per_page=20&sort=created&direction=desc").then(function (issues) {
      var pedidos = issues.filter(function (i) { return !i.pull_request && (i.body || "").indexOf(MARCA) >= 0; })
        .map(function (i) {
          return { n: i.number, nombre: campo(i.body, "Nombre") || "(sin nombre)", pista: campo(i.body, "Pista"),
                   abierta: i.state === "open", creada: i.created_at, cerrada: i.closed_at, url: i.html_url,
                   comentarios: i.comments };
        });
      return Promise.all(pedidos.map(function (p) {
        if (p.abierta) { p.estado = "en curso"; return p; }
        if (cache[p.n]) { p.estado = cache[p.n].estado; p.motivo = cache[p.n].motivo; return p; }
        if (!p.comentarios) { p.estado = "fallida"; p.motivo = "Terminó sin borrador."; return p; }
        return gh("/repos/" + REPO + "/issues/" + p.n + "/comments?per_page=5").then(function (cs) {
          var bot = cs.filter(function (c) { return c.user && /\[bot\]$|^github-actions/.test(c.user.login); }).pop() || cs[cs.length - 1];
          var c = clasificar(bot && bot.body);
          cache[p.n] = { estado: c.estado, motivo: c.motivo, texto: bot ? bot.body : "" };
          p.estado = c.estado; p.motivo = c.motivo; return p;
        });
      }));
    });
  }

  /* ── panel ─────────────────────────────────────────────────────────── */
  var ESTADOS = { "en curso": ["INVESTIGANDO", "curso"], lista: ["BORRADOR LISTO", "lista"],
                  detenida: ["SE DETUVO", "cola"], fallida: ["FALLÓ", "cola"] };

  function abrir() { openPanel("investigaciones", "INVESTIGACIONES", construir); }
  function construir(body) {
    pararReloj();
    if (!token()) return pantallaConectar(body);
    body.appendChild(el("div", { class: "fld" }, [
      el("button", { class: "mini go", style: "width:100%", type: "button", text: "+ INVESTIGAR UN NOMBRE",
                     onclick: function () { pedir(""); } })]));
    var caja = el("div");
    body.appendChild(caja);
    if (lista) pintar(caja, lista);
    else caja.appendChild(el("div", { class: "hint", text: "Leyendo tus investigaciones…" }));
    actualizar(caja);
    body.appendChild(el("div", { class: "hint ti-pie" }, [
      document.createTextNode("Conectado" + (usuario ? " como " + usuario : "") + ". "),
      el("button", { type: "button", class: "ti-link", text: "Desconectar este navegador",
                     onclick: function () { borrarToken(); renderRail(); abrir(); toast("Llave borrada de este navegador."); } })]));
  }
  function actualizar(caja) {
    leerPedidos().then(function (ps) {
      lista = ps; renderRail();
      if (modoPanel() !== "investigaciones" || !caja.isConnected) return;
      clr(caja); pintar(caja, ps);
      if (ps.some(function (p) { return p.abierta; })) reloj = setTimeout(function () { actualizar(caja); }, 30000);
    }).catch(function (e) {
      if (!caja.isConnected) return;
      clr(caja); caja.appendChild(el("div", { class: "hint ti-error", text: e.message }));
    });
  }
  function pararReloj() { if (reloj) { clearTimeout(reloj); reloj = null; } }

  function pintar(caja, ps) {
    if (!ps.length) {
      caja.appendChild(el("div", { class: "hint", text: "Todavía no pediste ninguna investigación. Pulsa «+ INVESTIGAR UN NOMBRE» o escribe un nombre en el buscador de arriba." }));
      return;
    }
    ps.forEach(function (p) {
      var e = ESTADOS[p.estado] || ESTADOS.fallida;
      var box = el("div", { class: "solq ti-item" + (p.estado === "lista" ? " ti-abre" : "") });
      box.appendChild(el("b", { text: p.nombre }));
      var meta = el("span");
      meta.appendChild(el("em", { class: e[1], text: e[0] }));
      meta.appendChild(document.createTextNode("  " + haceCuanto(p.creada).toUpperCase()));
      box.appendChild(meta);
      if (p.estado === "en curso")
        box.appendChild(el("div", { class: "nota", style: "color:var(--cy)",
          text: "Lleva " + minutos(p.creada) + " min. Suele tardar entre 5 y 45. Esta lista se actualiza sola." }));
      if (p.motivo) box.appendChild(el("div", { class: "nota", text: p.motivo }));
      var acc = el("div", { class: "ti-acc" });
      if (p.estado === "lista")
        acc.appendChild(el("button", { class: "mini go", type: "button", text: "LEER EL BORRADOR",
                                       onclick: function () { leer(p); } }));
      if (p.estado === "detenida" || p.estado === "fallida")
        acc.appendChild(el("button", { class: "mini", type: "button", text: "PEDIR DE NUEVO",
                                       onclick: function () { pedir(p.nombre, p.pista); } }));
      box.appendChild(acc);
      if (p.estado === "lista") box.addEventListener("click", function (ev) { if (ev.target.tagName !== "BUTTON") leer(p); });
      caja.appendChild(box);
    });
  }

  /* ── el borrador, dentro de la app ─────────────────────────────────── */
  function leer(p) {
    pararReloj();
    openPanel("investigaciones", "BORRADOR · " + p.nombre.toUpperCase(), function (body) {
      body.appendChild(el("div", { class: "fld" }, [
        el("button", { class: "mini", type: "button", text: "← VOLVER A LA LISTA", onclick: abrir })]));
      body.appendChild(el("div", { class: "ti-aviso",
        text: "BORRADOR SIN REVISAR. Abre cada enlace y confirma que la cita diga lo que se afirma antes de usarlo." }));
      var doc = el("div", { class: "ti-doc" }, [el("p", { text: "Cargando el borrador…" })]);
      body.appendChild(doc);
      var c = cache[p.n];
      var texto = c && c.texto ? Promise.resolve(c.texto)
        : gh("/repos/" + REPO + "/issues/" + p.n + "/comments?per_page=5").then(function (cs) {
            var b = cs[cs.length - 1]; return b ? b.body : "";
          });
      texto.then(function (t) {
        if (c && c.html) return c.html;
        return gh("/markdown", { metodo: "POST", texto: true, cuerpo: { text: t, mode: "gfm", context: REPO } })
          .then(function (h) { if (c) c.html = h; return h; });
      }).then(function (html) {
        clr(doc);
        var tpl = document.createElement("template");
        tpl.innerHTML = html;                     // HTML ya saneado por GitHub
        tpl.content.querySelectorAll("script,style,iframe,object,embed,form").forEach(function (n) { n.remove(); });
        tpl.content.querySelectorAll("a").forEach(function (a) {
          a.target = "_blank"; a.rel = "noopener noreferrer nofollow"; });
        tpl.content.querySelectorAll("strong").forEach(function (s) {
          var t = s.textContent.toLowerCase();
          if (/dudoso|no lo nombra/.test(t)) s.className = "ti-mal";
          else if (/una sola fuente|solo wikipedia/.test(t)) s.className = "ti-medio";
        });
        doc.appendChild(tpl.content);
      }).catch(function (e) { clr(doc); doc.appendChild(el("p", { class: "ti-error", text: e.message })); });
    });
  }

  /* ── pedir una investigación ───────────────────────────────────────── */
  function nombreCompleto(n) { return n.trim().split(/\s+/).length >= 2 && n.trim().length >= 5; }

  function pedir(pref, pistaPref) {
    if (!token()) { abrir(); toast("Primero conecta la app con el investigador.", 1); return; }
    openSheet("INVESTIGAR UN NOMBRE", function (f) {
      f.appendChild(el("div", { class: "tip", style: "margin:8px 0 2px;line-height:1.6",
        text: "El investigador busca en Wikidata, Wikipedia y prensa, y arma un borrador con la cita y el enlace de cada dato. Solo figuras públicas: no investiga a particulares, menores ni familiares." }));
      f.appendChild(lab("NOMBRE Y APELLIDO", inp("tiN", "Como aparece en la prensa: Xi Jinping, Peter Thiel…", pref || ""),
        "Escríbelo como se escribe, no como suena. Si dudas, usa «COMPROBAR EL NOMBRE»."));
      var sug = el("div", { class: "ti-sug" });
      f.appendChild(el("div", { class: "fld" }, [
        el("button", { class: "mini", type: "button", text: "COMPROBAR EL NOMBRE", onclick: function () { comprobar(sug); } })]));
      f.appendChild(sug);
      f.appendChild(lab("PISTA (OPCIONAL)", inp("tiP", "Cargo, empresa o país: Presidente de China", pistaPref || ""),
        "Solo hace falta si hay personas con el mismo nombre o es poco conocida."));
      f.appendChild(lab("DOCUMENTO (OPCIONAL)", inp("tiD", "https://… enlace público que pruebe su papel"),
        "Solo para personas que no están en Wikidata."));
    }, function () {
      var n = V("tiN"), pista = V("tiP"), docu = V("tiD");
      if (!nombreCompleto(n)) { $("sheetErr").textContent = "ESCRIBE NOMBRE Y APELLIDO."; return false; }
      if (docu && !/^https?:\/\//i.test(docu)) { $("sheetErr").textContent = "EL DOCUMENTO DEBE SER UN ENLACE QUE EMPIECE CON https://"; return false; }
      var cuerpo = "### Nombre y apellido\n\n" + n + "\n\n### Pista\n\n" + (pista || "_No response_") +
                   "\n\n### Documento\n\n" + (docu || "_No response_") + "\n";
      return gh("/repos/" + REPO + "/issues", { metodo: "POST", cuerpo: { title: "Investigación · " + n, body: cuerpo } })
        .then(function () {
          lista = null; toast("Pedido enviado. Verás el avance en INVESTIGACIONES.");
          setTimeout(abrir, 50);
        }, function (e) { $("sheetErr").textContent = e.message.toUpperCase(); return false; });
    }, "INVESTIGAR");
  }

  /* Wikidata da la grafía correcta y una descripción para no confundir homónimos.
     Se consulta solo al pulsar el botón. */
  function comprobar(caja) {
    var n = V("tiN");
    clr(caja);
    if (n.length < 3) { caja.appendChild(el("div", { class: "tip", text: "Escribe primero el nombre." })); return; }
    caja.appendChild(el("div", { class: "tip", text: "Buscando en Wikidata…" }));
    var u = "https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&origin=*&language=es&uselang=es&type=item&limit=6&search=" + encodeURIComponent(n);
    fetch(u).then(function (r) { return r.json(); }).then(function (j) {
      clr(caja);
      var rs = (j.search || []).filter(function (r) { return r.label; });
      if (!rs.length) {
        caja.appendChild(el("div", { class: "tip ti-error",
          text: "Wikidata no conoce ese nombre. Revisa cómo se escribe (por ejemplo «Xi Jinping», no «shi shin ping»). Si la persona no está en Wikidata, agrega un Documento." }));
        return;
      }
      caja.appendChild(el("div", { class: "tip", text: "Elige a la persona correcta:" }));
      rs.forEach(function (r) {
        caja.appendChild(el("button", { type: "button", class: "ti-opc", onclick: function () {
          $("tiN").value = r.label;
          if (r.description && !V("tiP")) $("tiP").value = r.description.slice(0, 80);
          clr(caja); caja.appendChild(el("div", { class: "tip", style: "color:var(--gr)", text: "Listo: «" + r.label + "»." }));
        } }, [el("b", { text: r.label }), el("span", { text: r.description || "sin descripción" })]));
      });
    }).catch(function () { clr(caja); caja.appendChild(el("div", { class: "tip ti-error", text: "No se pudo consultar Wikidata." })); });
  }

  /* ── conectar, una sola vez ────────────────────────────────────────── */
  function pantallaConectar(body) {
    body.appendChild(el("div", { class: "hint" }, [
      el("b", { text: "Pide investigaciones sin salir de Telar. " }),
      document.createTextNode("Escribes un nombre, el investigador arma un borrador con fuentes y lo lees aquí mismo. Es solo para quien administra Telar: hace falta conectar esta app con tu cuenta de GitHub una vez.")]));
    var pasos = el("ol", { class: "ti-pasos" });
    [["Abre ", el("a", { href: CREAR_TOKEN, target: "_blank", rel: "noopener noreferrer", text: "crear una llave en GitHub" }), "."],
     ["En «Repository access», elige ", el("b", { text: "Only select repositories" }), " y marca ", el("b", { text: "telar-investigador" }), "."],
     ["En «Permissions», pon ", el("b", { text: "Issues: Read and write" }), " y ", el("b", { text: "Contents: Read-only" }), "."],
     ["Pulsa ", el("b", { text: "Generate token" }), ", cópiala y pégala aquí abajo."]].forEach(function (partes) {
      pasos.appendChild(el("li", null, partes.map(function (x) { return typeof x === "string" ? document.createTextNode(x) : x; })));
    });
    body.appendChild(pasos);
    var campoT = el("input", { type: "password", placeholder: "github_pat_…", autocomplete: "off", "aria-label": "Llave de GitHub" });
    var err = el("div", { class: "hint ti-error" });
    var b = el("button", { class: "mini go", type: "button", text: "CONECTAR", onclick: function () {
      var t = campoT.value.trim();
      if (!t) { err.textContent = "Pega la llave primero."; return; }
      b.disabled = true; b.textContent = "COMPROBANDO…"; err.textContent = "";
      gh("/repos/" + REPO, { token: t }).then(function () {
        return gh("/user", { token: t }).then(function (u) { usuario = u.login; }, function () {});
      }).then(function () {
        if (!guardarToken(t)) throw new Error("Este navegador no deja guardar la llave (modo privado).");
        toast("Conectado. Ya puedes pedir investigaciones."); renderRail(); abrir();
      }).catch(function (e) { b.disabled = false; b.textContent = "CONECTAR"; err.textContent = e.message; });
    } });
    body.appendChild(el("div", { class: "fld ti-token" }, [campoT, b]));
    body.appendChild(err);
    body.appendChild(el("div", { class: "hint" }, [document.createTextNode(
      "La llave queda guardada solo en este navegador y viaja únicamente a GitHub. Puedes borrarla cuando quieras con «Desconectar», o revocarla en GitHub.")]));
  }

  /* ── botón lateral ─────────────────────────────────────────────────── */
  function renderRail() {
    var b = document.getElementById("rTI");
    if (!b) return;
    var old = b.querySelector(".badge"); if (old) old.remove();
    var n = (lista || []).filter(function (p) { return p.abierta; }).length;
    if (n) b.appendChild(el("span", { class: "badge", style: "background:var(--cy);color:#04070c", text: String(n) }));
  }
  function boton() {
    var b = railBtn("rTI", "INVESTIGACIONES", ["M10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12z", "M20 20l-5.6-5.6", "M10 7v6M7 10h6"],
      function () { togglePanel("investigaciones", "INVESTIGACIONES", construir); });
    setTimeout(renderRail, 0);
    return b;
  }

  /* ── estilos propios ───────────────────────────────────────────────── */
  var css = document.createElement("style");
  css.textContent = [
    ".ti-item{cursor:default}.ti-item.ti-abre{cursor:pointer}.ti-item.ti-abre:hover{background:rgba(52,223,232,.05)}",
    ".ti-acc{display:flex;gap:6px;margin-top:6px}.ti-acc:empty{display:none}",
    ".ti-error{color:var(--rd)!important}",
    ".ti-pie{margin-top:8px;border-top:1px solid var(--line);padding-top:9px}",
    ".ti-link{color:var(--txt-dim);text-decoration:underline;padding:0;font:inherit;letter-spacing:inherit}",
    ".ti-link:hover{color:var(--rd)}",
    ".ti-pasos{margin:2px 11px 10px 28px;font-size:calc(9px * var(--k));line-height:1.75;color:var(--txt-dim)}",
    ".ti-pasos a{color:var(--cy)}.ti-pasos b{color:var(--txt);font-weight:600}",
    ".ti-token{display:flex;gap:6px;padding:0 11px}.ti-token input{flex:1;min-width:0}",
    ".ti-aviso{margin:4px 11px 10px;padding:7px 9px;border:1px solid var(--am);color:var(--am);font-size:calc(8px * var(--k));letter-spacing:.1em;line-height:1.6}",
    ".ti-doc{padding:0 11px 14px;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:calc(10.5px * var(--k));line-height:1.6;color:var(--txt);letter-spacing:0}",
    ".ti-doc h1{font-size:1.5em;margin:.2em 0 .5em;color:#fff}",
    ".ti-doc h2{font-size:1.1em;margin:1.3em 0 .4em;padding-bottom:.25em;border-bottom:1px solid var(--line-2);color:var(--cy);letter-spacing:.04em}",
    ".ti-doc h3{font-size:1em;margin:1em 0 .2em;color:#fff}",
    ".ti-doc p{margin:.45em 0}.ti-doc ul{margin:.3em 0 .6em 1.1em}.ti-doc li{margin:.35em 0;color:var(--txt-dim)}",
    ".ti-doc a{color:var(--cy);text-decoration:underline;text-underline-offset:2px;overflow-wrap:anywhere}",
    ".ti-doc strong{color:#fff}.ti-doc .ti-mal{color:var(--rd)}.ti-doc .ti-medio{color:var(--am)}",
    ".ti-doc code{font-family:var(--mono);font-size:.9em}",
    ".ti-sug{padding:0 0 4px}",
    ".ti-opc{display:block;width:100%;text-align:left;padding:7px 9px;margin-top:5px;border:1px solid var(--line-2);color:var(--txt)}",
    ".ti-opc:hover{border-color:var(--cy)}.ti-opc b{display:block;font-weight:600}",
    ".ti-opc span{display:block;color:var(--txt-faint);font-size:.9em;margin-top:2px}"
  ].join("\n");
  document.head.appendChild(css);

  window.TelarInvestigador = { iniciar: iniciar, abrir: abrir, pedir: pedir, boton: boton, conectado: function () { return !!token(); } };
})();
