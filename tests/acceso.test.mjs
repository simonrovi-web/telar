// acceso/worker.js con GitHub simulado: entrega el permiso solo a quien administra Telar.
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../acceso/worker.js";

const env = { GITHUB_CLIENT_ID: "prueba", GITHUB_CLIENT_SECRET: "secreto", APP_URL: "https://simonrovi-web.github.io/telar/" };
const origen = "https://simonrovi-web.github.io";
let cuenta = "simonrovi-web";
globalThis.fetch = async (u, o = {}) => {
  u = String(u);
  if (u === "https://github.com/login/oauth/access_token") {
    const b = JSON.parse(o.body);
    if (b.client_secret !== "secreto") return Response.json({ error: "incorrect_client_credentials" });
    if (b.refresh_token === "viejo") return Response.json({ error: "bad_refresh_token" });
    return Response.json({ access_token: "ghu_" + (b.code || "renovado"), refresh_token: "ghr_nuevo", expires_in: 28800 });
  }
  if (u === "https://api.github.com/user") return Response.json({ login: cuenta });
  throw new Error("red no esperada: " + u);
};
const vuelta = async (q, e = env) => {
  const r = await worker.fetch(new Request("https://acceso.test/vuelta" + q), e);
  const loc = r.headers.get("Location");
  assert.equal(r.status, 302);
  assert.ok(loc.startsWith(e.APP_URL + "#ti="), "el permiso viaja en el fragmento");
  return JSON.parse(Buffer.from(decodeURIComponent(loc.split("#ti=")[1]), "base64").toString("utf8"));
};
const renovar = (refresh, o = origen) => worker.fetch(new Request("https://acceso.test/renovar",
  { method: "POST", headers: { Origin: o, "Content-Type": "application/json" }, body: JSON.stringify({ refresh }) }), env);

test("entrega permiso y estado a quien administra", async () => {
  cuenta = "simonrovi-web";
  const d = await vuelta("?code=abc&state=s1");
  assert.equal(d.token, "ghu_abc"); assert.equal(d.refresh, "ghr_nuevo"); assert.equal(d.state, "s1");
  assert.ok(d.vence > Date.now());
});
test("rechaza otras cuentas de GitHub", async () => {
  cuenta = "otra-persona";
  const d = await vuelta("?code=abc&state=s2");
  assert.ok(!d.token); assert.match(d.error, /no administra/);
  cuenta = "simonrovi-web";
});
test("sin código no hay permiso", async () => { assert.ok(!(await vuelta("?state=s3")).token); });
test("avisa si falta el secreto", async () => {
  assert.match((await vuelta("?code=abc", { ...env, GITHUB_CLIENT_SECRET: "" })).error, /no tiene configurada/);
});
test("renueva solo desde la app", async () => {
  const r = await renovar("ghr_x");
  assert.equal(r.status, 200); assert.equal((await r.json()).token, "ghu_renovado");
  assert.equal(r.headers.get("Access-Control-Allow-Origin"), origen);
  assert.equal((await renovar("ghr_x", "https://otro.example")).status, 403);
});
test("una renovación vencida pide entrar de nuevo", async () => {
  const r = await renovar("viejo");
  assert.equal(r.status, 401); assert.match((await r.json()).error, /venció/);
});
