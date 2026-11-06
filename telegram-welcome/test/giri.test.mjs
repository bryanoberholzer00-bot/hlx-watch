// Test della sorveglianza dei giri: node --test telegram-welcome/test/giri.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { GAP_MS, STUCK_MS, problemText, runProblem, runsStatus, watchRuns } from "../src/giri.js";

class FakeKV {
  constructor() { this.data = new Map(); this.writes = 0; }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); this.writes += 1; }
  async delete(k) { this.data.delete(k); }
}

const now = Date.parse("2026-11-06T21:30:00Z");
const ago = (min) => new Date(now - min * 60000).toISOString();
const run = (min, status, conclusion = null) => ({ created_at: ago(min), status, conclusion, html_url: `https://x/${min}` });

test("tutto a posto: giri recenti riusciti, o uno in corso da poco", () => {
  assert.equal(runProblem([], now), null);
  assert.equal(runProblem([run(5, "completed", "success"), run(35, "completed", "success")], now), null);
  assert.equal(runProblem([run(3, "in_progress"), run(33, "completed", "success")], now), null);
  // un fallimento solo è un intoppo, non un guasto
  assert.equal(runProblem([run(5, "completed", "failure"), run(35, "completed", "success")], now), null);
  // annullato a mano tra due fallimenti: non conta, ma i due fallimenti sì
  assert.equal(runProblem([run(5, "completed", "failure"), run(20, "completed", "cancelled"), run(35, "completed", "success")], now), null);
});

test("giro bloccato: in corso o in coda da più di 75 minuti", () => {
  const p = runProblem([run(80, "in_progress"), run(110, "completed", "success")], now);
  assert.equal(p.tipo, "bloccato");
  assert.equal(p.minuti, 80);
  assert.equal(runProblem([run(90, "queued"), run(120, "completed", "success")], now).tipo, "bloccato");
  assert.equal(runProblem([run(70, "in_progress")], now), null);
  assert.ok(STUCK_MS > 60 * 60000);              // l'archeologia a mano dura circa un'ora
});

test("due giri falliti di fila", () => {
  const p = runProblem([run(5, "completed", "failure"), run(20, "completed", "cancelled"), run(35, "completed", "failure")], now);
  assert.equal(p.tipo, "falliti");
  assert.equal(p.dal, ago(35));
});

test("nessun giro da più di 80 minuti, solo se l'avvio ogni 30 minuti è attivo", () => {
  const runs = [run(85, "completed", "success"), run(115, "completed", "success")];
  assert.equal(runProblem(runs, now).tipo, "fermo");
  assert.equal(runProblem(runs, now, false), null);
  assert.equal(runProblem([run(75, "completed", "success")], now), null);
  assert.ok(GAP_MS > 60 * 60000);
});

test("un avviso per guasto, uno quando passa, e niente scritture inutili", async () => {
  const kv = new FakeKV();
  const sent = [];
  const env = { SUBS: kv, OWNER_CHAT_ID: "1" };
  const tg = async (_e, chat, text) => { sent.push(text); };
  const stuck = [run(80, "in_progress"), run(110, "completed", "success")];
  assert.equal((await watchRuns(env, stuck, tg, now)).tipo, "bloccato");
  assert.equal((await watchRuns(env, stuck, tg, now + 600000)).tipo, "bloccato");
  assert.equal(sent.length, 1);
  assert.match(sent[0], /in corso da 80 minuti/);
  assert.equal(kv.writes, 1);
  assert.equal((await runsStatus(env)).tipo, "bloccato");
  // poi fallisce due volte: è un guasto diverso, nuovo avviso
  await watchRuns(env, [run(5, "completed", "failure"), run(35, "completed", "failure")], tg, now);
  assert.equal(sent.length, 2);
  assert.match(sent[1], /ultimi due giri sono falliti/);
  // torna a posto: un messaggio, e il guasto sparisce dalla pagina Stato
  assert.equal(await watchRuns(env, [run(2, "completed", "success"), run(5, "completed", "failure")], tg, now), null);
  assert.equal(sent.length, 3);
  assert.match(sent[2], /funzionano di nuovo/);
  assert.equal(await runsStatus(env), null);
  // tutto a posto e niente da cancellare: nessun messaggio
  await watchRuns(env, [run(2, "completed", "success")], tg, now);
  assert.equal(sent.length, 3);
});

test("i testi sono in italiano e portano al giro", () => {
  assert.match(problemText({ tipo: "fermo", minuti: 95, url: "https://x/1" }), /nessun giro da 95 minuti[\s\S]*https:\/\/x\/1/);
  assert.match(problemText({ tipo: "bloccato", minuti: 80, stato: "queued", url: "" }), /in coda da 80 minuti/);
  assert.match(problemText({ tipo: "falliti", dal: "2026-11-06T20:00:00Z", url: "" }), /dalle 21:00/);
});
