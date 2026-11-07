// Test della modalità annuncio: node --test telegram-welcome/test/annuncio.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { DURATA_MS, KEY, activate, modeStatus, statusText, switchOff } from "../src/annuncio.js";
import { HOT_EVERY_MS, dispatchRun, dispatchStatus } from "../src/dispatch.js";

class FakeKV {
  constructor() { this.data = new Map(); this.writes = 0; }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); this.writes += 1; }
  async delete(k) { this.data.delete(k); }
}

const t0 = Date.parse("2026-11-07T12:00:00Z");     // sabato 13:00 in Italia: non sono ore calde
const item = { x: "POSSIBILE ANNUNCIO: nuovo repository SteamTracking/GameTracking-HLX", u: "https://github.com/x", g: "SteamTracking" };

test("parte una volta, si allunga con le voci nuove, scade dopo 14 giorni", async () => {
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
  const sent = [];
  const tg = async (_e, _c, text) => sent.push(text);
  assert.equal(await modeStatus(env, t0), null);
  const m = await activate(env, [item], tg, t0);
  assert.equal(m.nuova, true);
  assert.equal(sent.length, 1);
  assert.match(sent[0], /annuncio_falso/);
  const writes = env.SUBS.writes;
  await activate(env, [item], tg, t0 + 3600e3);            // la stessa voce dal giro completo: niente
  assert.equal(env.SUBS.writes, writes);
  await activate(env, [{ x: "Half-Life 3 su Steam", u: "https://store" }], tg, t0 + 2 * 86400e3);
  assert.equal(sent.length, 1);                             // un solo messaggio di modalità
  const s = await modeStatus(env, t0 + 3 * 86400e3);
  assert.equal(s.giorno, 4);
  assert.equal(s.voci.length, 2);
  assert.equal(s.voci[0].x, "nuovo repository SteamTracking/GameTracking-HLX");
  assert.ok(await modeStatus(env, t0 + 2 * 86400e3 + DURATA_MS - 1000));
  assert.equal(await modeStatus(env, t0 + 2 * 86400e3 + DURATA_MS + 1000), null);
});

test("/annuncio_falso la spegne, e dopo può ripartire", async () => {
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
  const tg = async () => {};
  assert.equal(await switchOff(env, t0), false);
  await activate(env, [item], tg, t0);
  assert.equal(await switchOff(env, t0 + 1000), true);
  assert.equal(await modeStatus(env, t0 + 2000), null);
  assert.ok(JSON.parse(env.SUBS.data.get(KEY)).spenta);
  assert.equal((await activate(env, [item], tg, t0 + 5000)).nuova, true);   // un annuncio dopo: riparte
  assert.match(statusText(await modeStatus(env, t0 + 6000), "https://s/"), /giorno 1/);
  assert.match(statusText(null, "https://s/"), /Nessuna modalità/);
});

test("in modalità annuncio il giro riparte dopo 10 minuti anche fuori dalle ore calde", async () => {
  const realFetch = globalThis.fetch;
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1", GH_DISPATCH_TOKEN: "t" };
  globalThis.fetch = async (url, init = {}) => String(url).endsWith("/dispatches")
    ? new Response(null, { status: 204 })
    : new Response(JSON.stringify({ workflow_runs: [{ status: "completed", created_at: new Date(t0 - 9.5 * 60000).toISOString() }] }), { status: 200 });
  try {
    assert.equal((await dispatchRun(env, async () => {}, t0)).done, false);
    await activate(env, [item], async () => {}, t0);
    assert.equal((await dispatchRun(env, async () => {}, t0)).done, true);
    const st = await dispatchStatus(env, t0);
    assert.equal(st.ogni, 10);
    assert.equal(st.annuncio.giorno, 1);
    assert.ok(HOT_EVERY_MS < 10 * 60000);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("un possibile annuncio dal giro completo (notifiche del sito) accende la modalità", async () => {
  const { handlePush } = await import("../src/push.js");
  class KV extends FakeKV { async list() { return { keys: [], list_complete: true }; } }
  const env = { SUBS: new KV(), VAPID_PRIVATE_JWK: "{}", PUSH_SECRET: "s", OWNER_CHAT_ID: "1" };
  const sent = [];
  const req = new Request("https://bot/push/send", {
    method: "POST", headers: { Authorization: "Bearer s", "content-type": "application/json" },
    body: JSON.stringify({ cursor: null, message: { title: "HLX Watch: POSSIBILE ANNUNCIO di Valve", body: "x", url: "https://u",
      urgente: true, annuncio: true, voci: [{ x: "POSSIBILE ANNUNCIO: app Steam Half-Life 3", u: "https://store", g: "Steam" }] } }),
  });
  await handlePush(req, env, async (_e, _c, t) => sent.push(t));
  const m = JSON.parse(env.SUBS.data.get(KEY));
  assert.equal(m.fonte, "giro completo");
  assert.equal(m.voci[0].x, "app Steam Half-Life 3");
  assert.equal(sent.length, 1);
});
