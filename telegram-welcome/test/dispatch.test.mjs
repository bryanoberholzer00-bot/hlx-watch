// Test dell'avvio del giro completo: node --test telegram-welcome/test/dispatch.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { DISPATCH_EVERY_MS, dispatchRun, dispatchStatus, shouldDispatch } from "../src/dispatch.js";
import { ORGS, compare } from "../src/fast.js";

class FakeKV {
  constructor() { this.data = new Map(); }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); }
  async delete(k) { this.data.delete(k); }
}

const now = Date.parse("2026-10-30T20:00:00Z");
const ago = (min) => new Date(now - min * 60000).toISOString();

test("avvia solo se l'ultimo run è finito da mezz'ora", () => {
  assert.equal(shouldDispatch([], now).go, true);
  assert.equal(shouldDispatch([{ status: "completed", created_at: ago(31) }], now).go, true);
  assert.equal(shouldDispatch([{ status: "completed", created_at: ago(29.5) }], now).go, true);
  assert.equal(shouldDispatch([{ status: "completed", created_at: ago(20) }], now).go, false);
  assert.equal(shouldDispatch([{ status: "in_progress", created_at: ago(60) }], now).go, false);
  assert.equal(shouldDispatch([{ status: "queued", created_at: ago(90) }, { status: "completed", created_at: ago(95) }], now).go, false);
  assert.ok(DISPATCH_EVERY_MS < 30 * 60000);      // con il cron ogni 10 minuti il run riparte dopo 30, non dopo 40
});

function mockGitHub(listStatus, runs, dispatchStatusCode = 204) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || "GET", auth: init.headers && init.headers.Authorization });
    if (String(url).endsWith("/dispatches")) return new Response(null, { status: dispatchStatusCode });
    return new Response(JSON.stringify({ workflow_runs: runs }), { status: listStatus });
  };
  return calls;
}

test("senza token non fa niente", async () => {
  const realFetch = globalThis.fetch;
  const calls = mockGitHub(200, []);
  try {
    const r = await dispatchRun({ SUBS: new FakeKV() }, async () => {}, now);
    assert.equal(r.done, false);
    assert.equal(calls.length, 0);
    assert.deepEqual(await dispatchStatus({ SUBS: new FakeKV() }), { attivo: false, token: false, errore: null, scadenza: null });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("avvia il run, e avvisa una volta sola se il token viene rifiutato", async () => {
  const realFetch = globalThis.fetch;
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1", GH_DISPATCH_TOKEN: "t" };
  const sent = [];
  const tg = async (_env, _chat, text) => sent.push(text);
  try {
    let calls = mockGitHub(200, [{ status: "completed", created_at: ago(40) }]);
    assert.equal((await dispatchRun(env, tg, now)).done, true);
    const post = calls.find((c) => c.method === "POST");
    assert.match(post.url, /actions\/workflows\/hlx-watch\.yml\/dispatches$/);
    assert.equal(post.auth, "Bearer t");

    calls = mockGitHub(200, [{ status: "completed", created_at: ago(5) }]);
    assert.equal((await dispatchRun(env, tg, now)).done, false);
    assert.equal(calls.filter((c) => c.method === "POST").length, 0);

    mockGitHub(401, []);
    await dispatchRun(env, tg, now);
    await dispatchRun(env, tg, now);
    assert.equal(sent.length, 1);
    assert.match(sent[0], /token GH_DISPATCH_TOKEN/);
    assert.equal((await dispatchStatus(env)).attivo, false);

    mockGitHub(200, [{ status: "completed", created_at: ago(40) }]);
    assert.equal((await dispatchRun(env, tg, now)).done, true);
    assert.equal(sent.length, 2);
    assert.match(sent[1], /di nuovo/);
    assert.deepEqual(await dispatchStatus(env), { attivo: true, token: true, errore: null, scadenza: null });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("repository nuovi di ValveSoftware: il primo giro registra, poi avvisa solo per Half-Life", () => {
  const valve = ORGS.find((o) => o.org === "ValveSoftware");
  const old = { repos: ["SteamTracking/GameTracking-CS2"], apps: {} };    // stato salvato prima di ValveSoftware
  const first = compare(old, [], { repos: [], valve: [{ full_name: "ValveSoftware/Proton", name: "Proton" }] }, now);
  assert.equal(first.alerts.length, 0);
  assert.deepEqual(first.state.repos, ["SteamTracking/GameTracking-CS2"]);
  const list = [{ full_name: "ValveSoftware/Proton", name: "Proton" },
    { full_name: "ValveSoftware/steam-tools", name: "steam-tools", description: "tools" },
    { full_name: "ValveSoftware/hlx-sdk", name: "hlx-sdk", html_url: "https://github.com/ValveSoftware/hlx-sdk", created_at: ago(3) }];
  const second = compare(first.state, [], { valve: list }, now);
  assert.equal(second.alerts.length, 1);
  assert.equal(second.alerts[0].g, valve.label);
  assert.match(second.alerts[0].x, /ValveSoftware\/hlx-sdk/);
  assert.deepEqual(second.state.repos, ["SteamTracking/GameTracking-CS2"]);   // SteamTracking non letto: invariato
});

test("scadenza del token: avviso 7 giorni prima e il giorno prima, una volta sola", async () => {
  const { checkExpiry, parseExpiry } = await import("../src/dispatch.js");
  assert.equal(parseExpiry("2026-11-29 12:00:00 UTC"), Date.parse("2026-11-29T12:00:00Z"));
  assert.equal(parseExpiry("2026-11-29 05:00:00 -0700"), Date.parse("2026-11-29T12:00:00Z"));
  assert.ok(Number.isNaN(parseExpiry(null)));
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1", GH_DISPATCH_TOKEN: "t" };
  const sent = [];
  const tg = async (_e, _c, text) => sent.push(text);
  const exp = "2026-11-29 12:00:00 UTC";
  const at = (days) => Date.parse("2026-11-29T12:00:00Z") - days * 86400000;
  assert.equal(await checkExpiry(env, exp, tg, at(20)), 20);
  assert.equal(sent.length, 0);
  assert.equal((await dispatchStatus(env)).scadenza, "2026-11-29");
  await checkExpiry(env, exp, tg, at(6.5));
  await checkExpiry(env, exp, tg, at(6));
  assert.equal(sent.length, 1);
  assert.match(sent[0], /scade tra 6 giorni \(29\/11\/2026\)/);
  await checkExpiry(env, exp, tg, at(0.5));
  await checkExpiry(env, exp, tg, at(0.4));
  assert.equal(sent.length, 2);
  assert.match(sent[1], /scade oggi/);
  // token rinnovato: data nuova, nessun avviso finché non si avvicina
  await checkExpiry(env, "2027-11-29 12:00:00 UTC", tg, at(0.3));
  assert.equal(sent.length, 2);
  assert.equal((await dispatchStatus(env)).scadenza, "2027-11-29");
});

test("il giro legge la scadenza dalle risposte di GitHub", async () => {
  const realFetch = globalThis.fetch;
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1", GH_DISPATCH_TOKEN: "t" };
  globalThis.fetch = async (url) => String(url).endsWith("/dispatches") ? new Response(null, { status: 204 })
    : new Response(JSON.stringify({ workflow_runs: [] }), { status: 200,
      headers: { "github-authentication-token-expiration": "2026-12-31 00:00:00 UTC" } });
  try {
    await dispatchRun(env, async () => {}, now);
    assert.equal((await dispatchStatus(env)).scadenza, "2026-12-31");
  } finally {
    globalThis.fetch = realFetch;
  }
});
