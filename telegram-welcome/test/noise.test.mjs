// Test del pulsante "è rumore": node --test telegram-welcome/test/noise.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { ensureWebhook, handleCallback, noiseList, parseData, updatedKeyboard } from "../src/noise.js";

class FakeKV {
  constructor() { this.data = new Map(); }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); }
}

function recorder() {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ method: String(url).split("/").pop(), body: init.body ? JSON.parse(init.body) : null });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  return { calls, fn };
}

const keyboard = { inline_keyboard: [[{ text: "👎 1", callback_data: "r:101" }, { text: "👎 2", callback_data: "r:102" }]] };

test("callback_data e pulsanti", () => {
  assert.equal(parseData("r:101"), "101");
  assert.equal(parseData("x:1"), null);
  assert.equal(parseData("r:abc"), null);
  const k = updatedKeyboard(keyboard, "r:102", true);
  assert.deepEqual(k.inline_keyboard[0].map((b) => b.text), ["👎 1", "↩️ 2"]);
  assert.deepEqual(updatedKeyboard(k, "r:102", false).inline_keyboard[0].map((b) => b.text), ["👎 1", "👎 2"]);
});

test("il proprietario segna e annulla; gli altri no", async () => {
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "42", BOT_TOKEN: "t" };
  const { calls, fn } = recorder();
  const cq = (from) => ({ id: "c1", data: "r:101", from: { id: from }, message: { chat: { id: 42 }, message_id: 7, reply_markup: keyboard } });
  assert.equal(await handleCallback(env, cq(99), fn), null);
  assert.match(calls[0].body.text, /Solo il proprietario/);
  assert.equal(await handleCallback(env, cq(42), fn), true);
  let list = await (await noiseList(env)).json();
  assert.deepEqual(Object.keys(list.voti), ["101"]);
  const edit = calls.find((c) => c.method === "editMessageReplyMarkup");
  assert.equal(edit.body.reply_markup.inline_keyboard[0][0].text, "↩️ 1");
  assert.equal(await handleCallback(env, cq(42), fn), false);
  list = await (await noiseList(env)).json();
  assert.deepEqual(list.voti, {});
});

test("il webhook riceve anche i pulsanti", async () => {
  const env = { BOT_TOKEN: "t", WEBHOOK_SECRET: "s" };
  const calls = [];
  const fn = async (url, init = {}) => {
    const method = String(url).split("/").pop();
    calls.push({ method, body: init.body ? JSON.parse(init.body) : null });
    if (method === "getWebhookInfo") {
      return new Response(JSON.stringify({ ok: true, result: { url: "https://bot/x", allowed_updates: ["message"] } }));
    }
    return new Response(JSON.stringify({ ok: true }));
  };
  assert.equal(await ensureWebhook(env, fn), true);
  const set = calls.find((c) => c.method === "setWebhook");
  assert.deepEqual(set.body, { url: "https://bot/x", secret_token: "s", allowed_updates: ["message", "callback_query"] });
  const fn2 = async () => new Response(JSON.stringify({ ok: true, result: { url: "https://bot/x", allowed_updates: ["message", "callback_query"] } }));
  assert.equal(await ensureWebhook(env, fn2), false);
});
