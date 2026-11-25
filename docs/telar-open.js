/* ═══════════════════════════════════════════════════════════════════════
   TELAR · TALLER (edición en este navegador, con IA abierta)
   ───────────────────────────────────────────────────────────────────────
   Este módulo corre ANTES que la app. Hace dos cosas:

   1) Monta "window.telarLocal": una base de datos que vive en el
      navegador (localStorage), sembrada desde data/registro.json. Así la
      app completa funciona sin ningún servicio externo: se puede leer,
      editar, exportar y guardar, todo en tu propio equipo.

   2) Ofrece un motor de CÓDIGO ABIERTO (Gemma 4, Apache 2.0) que
      corre localmente en el navegador (WebLLM). Como ese modelo NO navega
      la web ni abre registros, NO puede producir datos con fuente: por eso
      sólo genera PISTAS DE INVESTIGACIÓN marcadas como NO VERIFICADAS, que
      una persona debe confirmar con una fuente real antes de registrar
      nada. Telar no inventa afirmaciones sobre personas reales.

   Licencia: MIT. Cámbialo a gusto.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  window.TELAR_OPEN = true;

  /* ───────── base de datos local (shim estilo colección/documento) ───────── */
  var LSKEY = "telar.open.v1";
  var COLS = ["personas", "organizaciones", "vinculos", "notas", "acciones", "solicitudes", "teorias"];
  var store = {};
  COLS.forEach(function (c) { store[c] = {}; });
  var listeners = {}; COLS.forEach(function (c) { listeners[c] = []; });
  var ready = false;

  function persist() { try { localStorage.setItem(LSKEY, JSON.stringify(store)); } catch (e) {} }
  function load() { try { var s = localStorage.getItem(LSKEY); return s ? JSON.parse(s) : null; } catch (e) { return null; } }
  function snap(coll) {
    var m = store[coll] || {};
    return { docs: Object.keys(m).map(function (id) { return { id: id, data: function () { return m[id]; } }; }) };
  }
  function emit(coll) { (listeners[coll] || []).forEach(function (cb) { try { cb(snap(coll)); } catch (e) {} }); }
  function emitAll() { COLS.forEach(emit); }

  function collection(name) {
    return {
      _n: name,
      limit: function () { return this; },
      onSnapshot: function (cb, err) {
        if (!listeners[name]) listeners[name] = [];
        listeners[name].push(cb);
        if (ready) { try { cb(snap(name)); } catch (e) { if (err) err(e); } }
        return function () { var i = listeners[name].indexOf(cb); if (i >= 0) listeners[name].splice(i, 1); };
      }
    };
  }
  function docRef(path) {
    var i = String(path).indexOf("/");
    var coll = path.slice(0, i), id = path.slice(i + 1);
    return {
      set: function (obj) { if (!store[coll]) store[coll] = {}; store[coll][id] = obj; persist(); emit(coll); return Promise.resolve(); },
      delete: function () { if (store[coll]) delete store[coll][id]; persist(); emit(coll); return Promise.resolve(); }
    };
  }
  var localDb = { collection: collection, doc: docRef };

  var downloads = {
    save: function (o) {
      try {
        var bl = new Blob([o.data], { type: "text/markdown" }), a = document.createElement("a");
        a.href = URL.createObjectURL(bl); a.download = o.filename || "telar.md";
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
        return Promise.resolve();
      } catch (e) { return Promise.reject(e); }
    }
  };
  var user = { can: function () { return Promise.resolve(true); }, canEdit: function () { return Promise.resolve(true); } };

  window.telarLocal = {
    use: function (name) {
      if (name === "db") return Promise.resolve(localDb);
      if (name === "downloads") return Promise.resolve(downloads);
      if (name === "user") return Promise.resolve(user);
      return Promise.resolve(null); // sin "mcp": no hay tarea remota en la edición abierta
    }
  };

  /* sembrar: localStorage si existe; si no, data/registro.json */
  var saved = load();
  if (saved && (saved.personas || saved.organizaciones || saved.vinculos)) {
    COLS.forEach(function (c) { store[c] = saved[c] || {}; });
    ready = true; setTimeout(emitAll, 0);
  } else {
    fetch("data/registro.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      if (j) {
        store.personas = j.personas || {};
        store.organizaciones = j.organizaciones || {};
        store.vinculos = j.vinculos || {};
        store.teorias = j.teorias || {};
      }
      ready = true; persist(); emitAll();
    }).catch(function () { ready = true; emitAll(); });
  }

  /* exponer un reinicio para quien quiera volver a la instantánea original */
  window.telarReset = function () { try { localStorage.removeItem(LSKEY); } catch (e) {} location.reload(); };

  /* ───────── IA abierta (WebLLM): genera pistas NO verificadas ───────── */
  /* Gemma 4 E2B, Apache 2.0. Sin shader-f16 en la GPU se usa la variante f32. */
  var MODELOS = { f16: "gemma-4-E2B-it-q4f16_1-MLC", f32: "gemma-4-E2B-it-q4f32_1-MLC" };
  var PESO = "unos 2,8 GB";
  var engine = null, cargando = null;

  function modal() {
    var ov = document.createElement("div");
    ov.setAttribute("style", "position:fixed;inset:0;z-index:9999;background:rgba(2,5,9,.78);backdrop-filter:blur(3px);display:grid;place-items:center;padding:16px;font-family:ui-monospace,Menlo,monospace");
    var box = document.createElement("div");
    box.setAttribute("style", "width:min(620px,100%);max-height:calc(100dvh - 56px);overflow:auto;background:#060c15;border:1px solid rgba(120,190,210,.35);box-shadow:0 24px 70px rgba(0,0,0,.8);color:#cfe6ee");
    ov.appendChild(box);
    ov.addEventListener("click", function (e) { if (e.target === ov) document.body.removeChild(ov); });
    document.body.appendChild(ov);
    return { ov: ov, box: box, close: function () { if (ov.parentNode) ov.parentNode.removeChild(ov); } };
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); }

  function cabecera(titulo) {
    return '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 13px;border-bottom:1px solid rgba(120,190,210,.2)">' +
      '<b style="letter-spacing:.24em;color:#34dfe8;font-size:12px;font-weight:600">' + esc(titulo) + '</b>' +
      '<button data-x style="background:none;border:none;color:#4f6573;font-size:15px;cursor:pointer">✕</button></div>';
  }
  var AVISO = '<div style="margin:11px 13px;padding:8px 10px;border:1px solid rgba(255,179,64,.55);background:rgba(255,179,64,.07);color:#ffb340;font-size:11px;line-height:1.6;letter-spacing:.04em">' +
    '⚠ BORRADOR DE IA LOCAL · NO VERIFICADO · SIN FUENTE. Un modelo que corre en tu navegador no navega la web ni abre registros: ' +
    'puede equivocarse o inventar. Esto son <b>pistas para investigar</b>, no hechos. Confirma cada punto con una fuente pública real ' +
    'antes de registrar cualquier afirmación en Telar.</div>';

  function soloChrome(m, nombre) {
    m.box.innerHTML = cabecera("IA ABIERTA · NO DISPONIBLE") + AVISO +
      '<div style="padding:4px 13px 10px;font-size:12px;line-height:1.7;color:#7e97a5">' +
      'Tu navegador no tiene <b>WebGPU</b>, que el modelo abierto necesita para correr localmente. ' +
      'Prueba con Chrome o Edge de escritorio recientes (o Chrome en Android nuevo). ' +
      'Mientras tanto, aquí tienes una plantilla de líneas de investigación para «' + esc(nombre) + '» que puedes llenar a mano, con fuente:' +
      '<pre style="white-space:pre-wrap;margin-top:8px;padding:9px;background:rgba(0,0,0,.4);border:1px solid rgba(120,190,210,.2);color:#cfe6ee;font-size:11px;line-height:1.6">' +
      esc(plantilla(nombre)) + '</pre></div>' + pie(plantilla(nombre));
    wire(m);
  }
  function plantilla(n) {
    return "Investigar: " + n + " (confirmar cada línea con fuente pública)\n\n" +
      "CARGOS Y TRAYECTORIA\n- ¿Qué cargos públicos ejerció y en qué fechas? (buscar en boletines oficiales, biografías institucionales)\n\n" +
      "EMPRESAS Y PROPIEDAD\n- ¿Qué sociedades o directorios integró? (registro de comercio, memorias anuales, reguladores de valores)\n\n" +
      "DINERO Y POLÍTICA\n- ¿Aportes de campaña, lobby, contratos públicos? (registros de aportes, declaraciones de patrimonio)\n\n" +
      "AFILIACIONES DECLARADAS\n- ¿Partidos, gremios, fundaciones, think tanks que declaró? (su propia web, comunicados)\n\n" +
      "VÍNCULOS PERSONALES\n- Sólo si una fuente pública los documenta (parentesco, sociedades conjuntas).";
  }
  function pie(texto) {
    return '<div style="display:flex;gap:8px;justify-content:flex-end;padding:10px 13px;border-top:1px solid rgba(120,190,210,.2)">' +
      '<button data-copy style="background:none;border:1px solid rgba(120,190,210,.35);color:#cfe6ee;padding:6px 11px;font:inherit;font-size:11px;letter-spacing:.14em;cursor:pointer">COPIAR</button>' +
      '<button data-x2 style="background:none;border:1px solid rgba(52,223,232,.5);color:#34dfe8;padding:6px 11px;font:inherit;font-size:11px;letter-spacing:.14em;cursor:pointer">CERRAR</button></div>' +
      '<textarea data-txt style="position:absolute;left:-9999px">' + esc(texto) + '</textarea>';
  }
  function wire(m) {
    var x = m.box.querySelector("[data-x]"), x2 = m.box.querySelector("[data-x2]"), cp = m.box.querySelector("[data-copy]");
    if (x) x.onclick = m.close; if (x2) x2.onclick = m.close;
    if (cp) cp.onclick = function () {
      var t = m.box.querySelector("[data-txt]"); if (!t) return;
      try { navigator.clipboard.writeText(t.value); cp.textContent = "COPIADO"; }
      catch (e) { t.style.left = "13px"; t.style.position = "static"; t.style.width = "calc(100% - 26px)"; }
    };
  }

  window.telarOpenInvestigar = function (nombre) {
    nombre = String(nombre || "").trim() || "(sin nombre)";
    var m = modal();
    if (!navigator.gpu) { soloChrome(m, nombre); return Promise.resolve("sin-webgpu"); }
    if (!engine && !cargando) {
      m.box.innerHTML = cabecera("IA ABIERTA · GEMMA 4") + AVISO +
        '<div style="padding:4px 13px 10px;font-size:12px;line-height:1.7;color:#7e97a5">La primera vez se descarga Gemma 4 E2B (' + PESO +
        ', Apache 2.0) y queda guardado en este navegador. Necesita unos 3,4 GB de memoria gráfica; en la mayoría de los teléfonos no corre.' +
        '<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap"><button data-si style="background:none;border:1px solid rgba(52,223,232,.5);color:#34dfe8;padding:6px 11px;font:inherit;font-size:11px;letter-spacing:.14em;cursor:pointer">DESCARGAR Y USAR</button>' +
        '<button data-no style="background:none;border:1px solid rgba(120,190,210,.35);color:#cfe6ee;padding:6px 11px;font:inherit;font-size:11px;letter-spacing:.14em;cursor:pointer">SOLO LA PLANTILLA</button></div></div>';
      wire(m);
      m.box.querySelector("[data-si]").onclick = function () { correr(m, nombre); };
      m.box.querySelector("[data-no]").onclick = function () { mostrarPlantilla(m, nombre); };
      return Promise.resolve("por-confirmar");
    }
    return correr(m, nombre);
  };

  function mostrarPlantilla(m, nombre) {
    m.box.innerHTML = cabecera("LÍNEAS DE INVESTIGACIÓN") +
      '<div style="padding:10px 13px"><pre style="white-space:pre-wrap;margin:0;padding:9px;background:rgba(0,0,0,.4);border:1px solid rgba(120,190,210,.2);color:#cfe6ee;font-size:11px;line-height:1.6">' +
      esc(plantilla(nombre)) + '</pre></div>' + pie(plantilla(nombre));
    wire(m);
  }

  function correr(m, nombre) {
    m.box.innerHTML = cabecera("IA ABIERTA · PENSANDO") + AVISO +
      '<div data-prog style="padding:6px 13px 14px;font-size:12px;line-height:1.7;color:#7e97a5">Preparando el modelo abierto en tu navegador… la primera vez descarga los pesos (puede tardar varios minutos). Se queda en caché para la próxima.</div>';
    wire(m);
    var prog = m.box.querySelector("[data-prog]");

    return Promise.resolve().then(function () {
      if (engine) return engine;
      if (cargando) return cargando;
      cargando = Promise.all([import("https://esm.run/@mlc-ai/web-llm@0.2.89"), navigator.gpu.requestAdapter()]).then(function (r) {
        var webllm = r[0], gpu = r[1];
        if (!gpu) throw new Error("sin GPU disponible");
        return webllm.CreateMLCEngine(gpu.features.has("shader-f16") ? MODELOS.f16 : MODELOS.f32, {
          initProgressCallback: function (p) { if (prog) prog.textContent = (p && p.text) ? p.text : "Cargando modelo…"; }
        });
      }).then(function (e) { engine = e; return e; });
      return cargando;
    }).then(function (eng) {
      if (prog) prog.textContent = "Generando pistas de investigación…";
      return eng.chat.completions.create({
        temperature: 0.5,
        messages: [
          { role: "system", content: "Eres un asistente que ayuda a PLANEAR una investigación OSINT sobre una figura pública. NO tienes acceso a internet y NO conoces datos actuales verificados. NUNCA afirmes hechos como ciertos ni inventes cargos, fechas, cifras o fuentes. Devuelve SOLO líneas de investigación e hipótesis a verificar, agrupadas en: CARGOS Y TRAYECTORIA, EMPRESAS Y PROPIEDAD, DINERO Y POLÍTICA, AFILIACIONES DECLARADAS, VÍNCULOS PERSONALES. Cada punto es una PREGUNTA o PISTA con dónde buscarla (tipo de registro o fuente), no un dato afirmado. Español, viñetas breves." },
          { role: "user", content: "Figura pública a investigar: " + nombre + ". Dame las líneas de investigación." }
        ]
      });
    }).then(function (res) {
      var txt = (res && res.choices && res.choices[0] && res.choices[0].message && res.choices[0].message.content) || "(sin respuesta del modelo)";
      m.box.innerHTML = cabecera("PISTAS PARA «" + nombre.toUpperCase() + "»") + AVISO +
        '<div style="padding:4px 13px 10px"><pre style="white-space:pre-wrap;margin:0;padding:9px;background:rgba(0,0,0,.4);border:1px solid rgba(120,190,210,.2);color:#cfe6ee;font-size:11.5px;line-height:1.65">' +
        esc(txt) + '</pre></div>' + pie(txt);
      wire(m);
      return "ia-abierta";
    }).catch(function (e) {
      cargando = null;
      m.box.innerHTML = cabecera("IA ABIERTA · ERROR") + AVISO +
        '<div style="padding:4px 13px 12px;font-size:12px;line-height:1.7;color:#7e97a5">No se pudo correr el modelo local (' + esc((e && e.message) || "error") +
        '). Suele deberse a falta de WebGPU, poca memoria o la descarga interrumpida. Mientras tanto, usa esta plantilla a mano, con fuente:' +
        '<pre style="white-space:pre-wrap;margin-top:8px;padding:9px;background:rgba(0,0,0,.4);border:1px solid rgba(120,190,210,.2);color:#cfe6ee;font-size:11px;line-height:1.6">' +
        esc(plantilla(nombre)) + '</pre></div>' + pie(plantilla(nombre));
      wire(m);
      return "ia-error";
    });
  }
})();
