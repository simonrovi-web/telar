/* Telar · acceso con GitHub (Cloudflare Worker).

   La app es una página estática: no puede guardar el secreto de la app de GitHub.
   Este servidor mínimo hace lo único que necesita ese secreto: cambiar el código que
   entrega GitHub al autorizar por un permiso de uso, y renovarlo cuando vence.
   No guarda nada. No ve los nombres que se investigan.

   Variables (Cloudflare → el Worker → Settings → Variables and Secrets):
     GITHUB_CLIENT_ID      Client ID de la app de GitHub «Telar»
     GITHUB_CLIENT_SECRET  Client secret de esa app (como Secret)
     APP_URL               https://simonrovi-web.github.io/telar/   (opcional, ese es el valor por omisión)
     USUARIOS              cuentas que pueden entrar, separadas por coma (opcional, por omisión simonrovi-web)

   Rutas:
     GET  /vuelta    GitHub vuelve aquí tras autorizar; reenvía a la app con el permiso en el fragmento (#)
     POST /renovar   la app pide un permiso nuevo cuando el anterior venció */

const APP_POR_OMISION = "https://simonrovi-web.github.io/telar/";
const USUARIOS_POR_OMISION = "simonrovi-web";

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const app = env.APP_URL || APP_POR_OMISION;
    const origen = new URL(app).origin;
    const cors = {
      "Access-Control-Allow-Origin": origen,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };

    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    if (url.pathname === "/vuelta" && req.method === "GET") {
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state") || "";
      if (!code) return volver(app, { error: "GitHub no entregó el permiso. Vuelve a intentarlo.", state });
      try {
        const t = await canjear(env, { code });
        await comprobarUsuario(env, t.token);
        return volver(app, { ...t, state });
      } catch (e) {
        return volver(app, { error: e.message, state });
      }
    }

    if (url.pathname === "/renovar" && req.method === "POST") {
      if (req.headers.get("Origin") !== origen) return json({ error: "Origen no permitido." }, 403, cors);
      try {
        const { refresh } = await req.json();
        if (!refresh) return json({ error: "Falta el permiso de renovación." }, 400, cors);
        const t = await canjear(env, { grant_type: "refresh_token", refresh_token: refresh });
        return json(t, 200, cors);
      } catch (e) {
        return json({ error: e.message }, 401, cors);
      }
    }

    return new Response("Telar · acceso con GitHub", { status: 404 });
  },
};

async function canjear(env, extra) {
  if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET)
    throw new Error("El servidor de acceso no tiene configurada la app de GitHub.");
  const r = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "telar-acceso" },
    body: JSON.stringify({ client_id: env.GITHUB_CLIENT_ID, client_secret: env.GITHUB_CLIENT_SECRET, ...extra }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error || !j.access_token)
    throw new Error(j.error === "bad_refresh_token" ? "La sesión venció. Entra de nuevo."
      : "GitHub no aceptó el permiso" + (j.error_description ? ": " + j.error_description : "."));
  return {
    token: j.access_token,
    refresh: j.refresh_token || "",
    vence: j.expires_in ? Date.now() + j.expires_in * 1000 : 0,
  };
}

async function comprobarUsuario(env, token) {
  const permitidos = (env.USUARIOS || USUARIOS_POR_OMISION).split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  const r = await fetch("https://api.github.com/user", {
    headers: { "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "telar-acceso" },
  });
  const u = await r.json().catch(() => ({}));
  if (!r.ok || !u.login) throw new Error("No se pudo confirmar tu cuenta de GitHub.");
  if (!permitidos.includes(u.login.toLowerCase()))
    throw new Error("La cuenta " + u.login + " no administra Telar.");
}

/* El permiso viaja en el fragmento (#): el navegador no lo envía a ningún servidor. */
function volver(app, datos) {
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(datos))));
  return new Response(null, { status: 302, headers: { "Location": app + "#ti=" + encodeURIComponent(b64), "Cache-Control": "no-store" } });
}

function json(datos, status, cors) {
  return new Response(JSON.stringify(datos), { status, headers: { ...cors, "Content-Type": "application/json" } });
}
