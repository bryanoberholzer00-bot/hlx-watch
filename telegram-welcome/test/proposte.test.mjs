// Test dei pulsanti delle proposte della revisione: node --test telegram-welcome/test/proposte.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { decisionsList, handleProposal, markedKeyboard, parseProposal } from "../src/proposte.js";
import { parseData } from "../src/noise.js";

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

const keyboard = { inline_keyboard: [
  [{ text: "✅ Applica 1", callback_data: "p:p2611091:s" }, { text: "❌ No 1", callback_data: "p:p2611091:n" }],
  [{ text: "✅ Applica 2", callback_data: "p:p2611092:s" }, { text: "❌ No 2", callback_data: "p:p2611092:n" }],
] };

test("callback_data delle proposte, distinti da quelli del 👎", () => {
  assert.deepEqual(parseProposal("p:p2611091:s"), { id: "p2611091", d: "s" });
  assert.deepEqual(parseProposal("p:p2611092:n"), { id: "p2611092", d: "n" });
  assert.equal(parseProposal("p:p2611091:x"), null);
  assert.equal(parseProposal("r:101"), null);
  assert.equal(parseData("p:p2611091:s"), null);
  const k = markedKeyboard(keyboard, "p2611091", "s");
  assert.deepEqual(k.inline_keyboard[0].map((b) => b.text), ["✅ Approvata 1", "❌ No 1"]);
  assert.deepEqual(k.inline_keyboard[1].map((b) => b.text), ["✅ Applica 2", "❌ No 2"]);
  const again = markedKeyboard(k, "p2611091", "n");
  assert.deepEqual(again.inline_keyboard[0].map((b) => b.text), ["✅ Applica 1", "❌ Scartata 1"]);
});

test("solo il proprietario sceglie; si può cambiare idea; /proposte.json", async () => {
  const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "42", BOT_TOKEN: "t" };
  const { calls, fn } = recorder();
  const cq = (from, data) => ({ id: "c1", data, from: { id: from }, message: { chat: { id: 42 }, message_id: 7, reply_markup: keyboard } });
  assert.equal(await handleProposal(env, cq(99, "p:p2611091:s"), fn), null);
  assert.match(calls[0].body.text, /Solo il proprietario/);
  assert.deepEqual(await handleProposal(env, cq(42, "p:p2611091:s"), fn, Date.UTC(2026, 10, 9, 10)), { id: "p2611091", d: "s" });
  assert.match(calls[1].body.text, /Approvata/);
  assert.equal(calls[2].method, "editMessageReplyMarkup");
  await handleProposal(env, cq(42, "p:p2611091:n"), fn, Date.UTC(2026, 10, 9, 11));
  const list = await (await decisionsList(env)).json();
  assert.deepEqual(Object.keys(list.decisioni), ["p2611091"]);
  assert.equal(list.decisioni.p2611091.d, "n");
});
