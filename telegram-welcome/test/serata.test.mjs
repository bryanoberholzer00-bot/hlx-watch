// Test della serata di un evento e delle pagine di half-life.com: node --test telegram-welcome/test
import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldDispatch, HOT_EVERY_MS } from "../src/dispatch.js";
import { fastCheck } from "../src/fast.js";
import {
  LIST, STATE, activeSerata, classifyApp, closingText, compareSerata, endedSerata, halfLifePaths, isGamePage,
  openingText, pagella, romeTime, runsSummary, serataCheck, serataStatus, uploadsPlaylist, valveList, videos,
  COMMENT_CHANNELS, videoText,
} from "../src/serata.js";

const S = { id: "prova", nome: "The Game Awards 2026", inizio: "2026-12-10T17:00:00Z", spettacolo: "2026-12-11T00:30:00Z",
  fine: "2026-12-11T08:00:00Z", previsione: "gr4", youtube: [{ id: "UCtga", nome: "The Game Awards" }] };
const during = Date.parse("2026-12-11T01:00:00Z");

class FakeKV {
  constructor() { this.data = new Map(); this.writes = 0; }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); this.writes += 1; }
  async delete(k) { this.data.delete(k); }
  async list() { return { keys: [], list_complete: true }; }
}

const home = (extra = "") => `<a href="https://www.half-life.com/en/alyx">A</a><a href="https://www.half-life.com/en/halflife2/20th">B</a>
<a href="https://www.half-life.com/en/home/">H</a><a href="https://www.half-life.com/it/home/">I</a><a href="/en/halflife25">C</a>${extra}`;
const valvePage = (ids, total = ids.length) => ({ success: 1, total_count: total,
  results_html: ids.map((id) => `<a data-ds-appid=\\"${id}\\">`).join("") });

test("la serata vera è valida e cade la notte del 10 dicembre", () => {
  const tga = LIST.find((s) => s.id === "tga-2026");
  assert.ok(tga);
  assert.ok(Date.parse(tga.inizio) < Date.parse(tga.spettacolo) && Date.parse(tga.spettacolo) < Date.parse(tga.fine));
  assert.equal(romeTime(tga.spettacolo), "1:30");
  assert.equal(romeTime(tga.inizio), "18:00");
  assert.equal(activeSerata(Date.parse("2026-12-11T02:00:00Z"))?.id, "tga-2026");
  assert.equal(activeSerata(Date.parse("2026-11-08T12:00:00Z")), null);
});

test("la prova generale: tre settimane prima, stessi orari, prima della serata vera", () => {
  const prova = LIST.find((s) => s.prova);
  const tga = LIST.find((s) => s.id === "tga-2026");
  assert.ok(prova && Date.parse(prova.fine) < Date.parse(tga.inizio));
  assert.equal(romeTime(prova.inizio), romeTime(tga.inizio));
  assert.equal(romeTime(prova.fine), romeTime(tga.fine));
  assert.equal(activeSerata(Date.parse("2026-11-19T22:00:00Z"))?.id, prova.id);
  assert.ok(prova.youtube.length);
});

test("finestra: prima, durante, dopo", () => {
  assert.equal(activeSerata(Date.parse("2026-12-10T16:59:00Z"), [S]), null);
  assert.equal(activeSerata(during, [S]).id, "prova");
  assert.equal(activeSerata(Date.parse(S.fine), [S]), null);
  assert.equal(endedSerata(Date.parse(S.fine) + 3600e3, [S]).id, "prova");
  assert.equal(endedSerata(Date.parse(S.fine) + 30 * 3600e3, [S]), null);
  assert.deepEqual(serataStatus(during, [S]), { id: "prova", nome: S.nome, spettacolo: S.spettacolo, fine: S.fine });
});

test("le pagine di half-life.com: giochi, anniversari, home e lingue", () => {
  assert.deepEqual(halfLifePaths(home()), ["alyx", "halflife2/20th", "halflife25"]);
  assert.ok(isGamePage("citadel"));
  assert.ok(!isGamePage("halflife25") && !isGamePage("halflife2/20th") && !isGamePage("halflife30th"));
});

test("letture: pagina di Valve e YouTube", () => {
  assert.deepEqual(valveList(valvePage(["620", "546560"], 3)), { dichiarati: 3, app: ["620", "546560"] });
  assert.equal(valveList({ success: 0 }), null);
  assert.equal(uploadsPlaylist("UCg0FSqPeiGD_lIiPaaAehQg"), "UUg0FSqPeiGD_lIiPaaAehQg");
  const v = videos({ items: [{ snippet: { title: "T", description: "D", publishedAt: "x", resourceId: { videoId: "abc" } } }, { snippet: {} }] });
  assert.deepEqual(v, [{ id: "abc", titolo: "T", testo: "D", quando: "x" }]);
});

test("confronto: la prima lettura impara, poi pagine, prodotti e video nuovi", () => {
  const canali = [{ id: "UCtga", nome: "The Game Awards", tutti: false }, { id: "UCv", nome: "Valve", tutti: true }];
  const first = compareSerata({}, { pagine: halfLifePaths(home()), valve: { tutti: valveList(valvePage(["1"])), arrivo: valveList(valvePage([])) },
    yt: { UCtga: [{ id: "a", titolo: "Old" }], UCv: [{ id: "b", titolo: "Steam Frame" }] } }, canali, during);
  assert.equal(first.annunci.length + first.note.length + first.nuoveApp.length, 0);
  assert.ok(first.changed);
  const same = compareSerata(first.state, { pagine: halfLifePaths(home()), valve: { tutti: valveList(valvePage(["1"])), arrivo: valveList(valvePage([])) },
    yt: { UCtga: [{ id: "a", titolo: "Old" }], UCv: [{ id: "b", titolo: "Steam Frame" }] } }, canali, during);
  assert.equal(same.changed, false);
  const next = compareSerata(first.state, {
    pagine: halfLifePaths(home('<a href="/en/citadel">X</a><a href="/en/alyx/soundtrack">Y</a>')),
    valve: { tutti: valveList(valvePage(["1", "2"])), arrivo: valveList(valvePage(["2"])) },
    yt: { UCtga: [{ id: "c", titolo: "HALF-LIFE 3 - Reveal Trailer | The Game Awards 2026" }, { id: "d", titolo: "Some Game Trailer" },
      { id: "e", titolo: "Valve presents" }, { id: "a", titolo: "Old" }], UCv: [{ id: "f", titolo: "Deadlock update" }, { id: "b", titolo: "Steam Frame" }] },
  }, canali, during);
  assert.deepEqual(next.annunci.map((a) => a.g), ["half-life.com", "YouTube: The Game Awards"]);
  assert.match(next.annunci[0].x, /^POSSIBILE ANNUNCIO: pagina nuova su half-life.com: \/en\/citadel/);
  assert.deepEqual(next.nuoveApp, ["2"]);
  assert.equal(next.note.length, 4);                     // pagina interna, prodotti in arrivo, "Valve presents", video di Valve
  assert.ok(next.note.some((n) => n.includes("/en/alyx/soundtrack")));
  assert.ok(next.note.some((n) => n.includes("passano da 0 a 1")));
  assert.ok(!next.note.some((n) => n.includes("Some Game Trailer")));
});

test("prodotto nuovo: Half-Life nel nome è un possibile annuncio", () => {
  assert.match(classifyApp("9", { name: "Half-Life: Citadel", type: "game" }).annuncio.x, /POSSIBILE ANNUNCIO: nuovo prodotto/);
  assert.match(classifyApp("9", { name: "HLX", type: "game" }).annuncio.u, /app\/9\//);
  assert.match(classifyApp("8", { name: "Steam Frame <Pro>", type: "hardware" }).nota, /Steam Frame &lt;Pro&gt;<\/a> \(hardware\)/);
  assert.match(classifyApp("7", null).nota, /app 7/);
});

test("messaggi di apertura e chiusura", () => {
  assert.match(openingText(S), /Stasera The Game Awards 2026[\s\S]*alle 1:30[\s\S]*Fino alle 9:00/);
  assert.match(closingText(S, []), /Nessuna traccia di Half-Life[\s\S]*registro delle previsioni/);
  assert.match(closingText(S, ["Video nuovo sul canale Valve"]), /è comparso:\n• Video nuovo/);
});

test("durante la serata il giro completo riparte ogni 10 minuti", () => {
  const runs = [{ status: "completed", created_at: new Date(during - 9 * 60e3).toISOString() }];
  assert.equal(shouldDispatch(runs, during, HOT_EVERY_MS).go, true);
});

function fakeFetch(world) {
  return async (url) => {
    const u = String(url);
    world.calls.push(u);
    const json = (x) => new Response(JSON.stringify(x), { status: 200 });
    if (u.startsWith("https://www.half-life.com/")) return new Response(home(world.extra), { status: 200 });
    if (u.includes("ajaxgetfilteredrecommendations")) return json(valvePage(u.includes("upcoming") ? world.arrivo : world.tutti));
    if (u.includes("/api/appdetails")) {
      const id = new URL(u).searchParams.get("appids");
      return json({ [id]: { success: true, data: { name: world.names[id] || `Gioco ${id}`, type: "game" } } });
    }
    if (u.includes("googleapis.com/youtube")) {
      const pl = new URL(u).searchParams.get("playlistId");
      return json({ items: (world.yt[pl] || []).map((v) => ({ snippet: { title: v.titolo, resourceId: { videoId: v.id } } })) });
    }
    if (u.includes("/actions/workflows/")) return world.runs ? json({ workflow_runs: world.runs }) : new Response("", { status: 500 });
    if (u.includes("api.github.com")) return json([]);
    return json({ appnews: { newsitems: [] } });
  };
}

test("serataCheck: apertura, prodotto Half-Life, chiusura, una scrittura per cambiamento", async () => {
  const realFetch = globalThis.fetch;
  const world = { calls: [], extra: "", tutti: ["1"], arrivo: [], names: {}, yt: {} };
  globalThis.fetch = fakeFetch(world);
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
    const sent = [];
    const tg = async (_e, _c, text) => sent.push(text);
    const before = Date.parse("2026-12-09T12:00:00Z");
    assert.deepEqual(await serataCheck(env, tg, before, [S]), []);           // prima: solo half-life.com
    assert.ok(!world.calls.some((u) => u.includes("ajaxgetfilteredrecommendations")));
    assert.equal(sent.length, 0);
    const quiet = env.SUBS.writes;
    await serataCheck(env, tg, before + 600e3, [S]);
    assert.equal(env.SUBS.writes, quiet);                                     // fuori dalla serata: niente di nuovo, niente scritture
    const start = Date.parse(S.inizio) + 60e3;
    assert.deepEqual(await serataCheck(env, tg, start, [S]), []);
    assert.equal(sent.length, 1);
    assert.match(sent[0], /Stasera The Game Awards 2026/);
    const writes = env.SUBS.writes;
    await serataCheck(env, tg, start + 600e3, [S]);
    assert.equal(env.SUBS.writes, writes + 1);                                // di sera: una scrittura, la pagella
    assert.equal(sent.length, 1);
    world.tutti = ["1", "42"];
    world.names = { 42: "Half-Life 3" };
    const found = await serataCheck(env, tg, during, [S]);
    assert.equal(found.length, 1);
    assert.match(found[0].x, /Half-Life 3/);
    await serataCheck(env, tg, Date.parse(S.fine) + 600e3, [S]);
    assert.match(sent.at(-1), /la serata è finita[\s\S]*è comparso:[\s\S]*Half-Life 3/);
    assert.match(sent.at(-1), /Pagella[\s\S]*controlli veloci: 3 su 90 attesi[\s\S]*GitHub non ha risposto/);   // senza token
    const n = sent.length;
    await serataCheck(env, tg, Date.parse(S.fine) + 1200e3, [S]);
    assert.equal(sent.length, n);                                             // chiusura una volta sola
    assert.deepEqual(JSON.parse(env.SUBS.data.get(STATE)).chiuse, ["prova"]);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("serataCheck: YouTube solo con la chiave, e una serata tranquilla si chiude con 'nessuna traccia'", async () => {
  const realFetch = globalThis.fetch;
  const world = { calls: [], extra: "", tutti: ["1"], arrivo: [], names: {}, yt: { UUtga: [{ id: "a", titolo: "Old" }] } };
  globalThis.fetch = fakeFetch(world);
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
    const sent = [];
    const tg = async (_e, _c, text) => sent.push(text);
    await serataCheck(env, tg, during, [S]);
    assert.ok(!world.calls.some((u) => u.includes("googleapis")));            // senza chiave niente YouTube
    env.YOUTUBE_API_KEY = "k";
    await serataCheck(env, tg, during + 600e3, [S]);
    assert.ok(world.calls.some((u) => u.includes("playlistId=UUtga")) && world.calls.some((u) => u.includes("UUg0FSqPeiGD")));
    await serataCheck(env, tg, Date.parse(S.fine) + 60e3, [S]);
    assert.match(sent.at(-1), /Nessuna traccia di Half-Life/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("fastCheck: una pagina nuova su half-life.com diventa un possibile annuncio", async () => {
  const realFetch = globalThis.fetch;
  const world = { calls: [], extra: "", tutti: [], arrivo: [], names: {}, yt: {} };
  globalThis.fetch = fakeFetch(world);
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
    const sent = [];
    const tg = async (_e, _c, text) => sent.push(text);
    await fastCheck(env, "https://example/", "https://bot", tg);
    assert.equal(sent.length, 0);
    world.extra = '<a href="https://www.half-life.com/en/citadel">X</a>';
    const r = await fastCheck(env, "https://example/", "https://bot", tg);
    assert.equal(r.alerts.length, 1);
    assert.match(sent[0], /POSSIBILE ANNUNCIO[\s\S]*half-life.com/);
    assert.ok(JSON.parse(env.SUBS.data.get("annuncio:modo")).voci[0].x.includes("citadel"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("pagella: giri completi dalla lista di GitHub", () => {
  const at = (m) => new Date(Date.parse(S.inizio) + m * 60e3).toISOString();
  const runs = [{ created_at: at(5), conclusion: "success" }, { created_at: at(15), conclusion: "failure" },
    { created_at: at(45), conclusion: null }, { created_at: at(-30), conclusion: "success" }];
  assert.deepEqual(runsSummary(runs, S), { avviati: 3, riusciti: 1, falliti: 1, altri: 1, buco: 855 });  // dall'ultimo alla fine
  assert.equal(runsSummary([], S).buco, 900);
});

test("pagella della prova: pronti, oppure cosa sistemare", () => {
  const P = { ...S, id: "p", nome: "Prova generale", prova: true };
  const buona = { controlli: 89, fonti: { "half-life.com": [89, 0], "pagina di Valve su Steam": [85, 4] } };
  const giri = { avviati: 88, riusciti: 88, falliti: 0, altri: 0, buco: 14 };
  assert.deepEqual(pagella(P, buona, giri).problemi, []);
  const ok = closingText(P, [], buona, giri);
  assert.match(ok, /^🧪 <b>Prova generale: finita/);
  assert.match(ok, /✅ controlli veloci: 89 su 90[\s\S]*✅ pagina di Valve su Steam: letta 85 volte su 89[\s\S]*✅ giri completi: 88 partiti/);
  assert.match(ok, /Tutto pronto per la serata vera/);
  assert.ok(!ok.includes("registro delle previsioni"));
  const male = closingText(P, [], { controlli: 50, fonti: { "YouTube The Game Awards": [10, 40] } },
    { avviati: 40, riusciti: 38, falliti: 2, altri: 0, buco: 70 });
  assert.match(male, /❌ controlli veloci: 50 su 90/);
  assert.match(male, /Da sistemare prima della serata vera:[\s\S]*cron di Cloudflare[\s\S]*YouTube The Game Awards non risponde[\s\S]*2 giri completi falliti[\s\S]*ogni 10 minuti/);
  assert.match(openingText(P), /^🧪 <b>Prova generale<\/b>[\s\S]*fino alle 9:00[\s\S]*pagella[\s\S]*l'avviso è vero/);
  assert.ok(!closingText(S, [], buona, giri).includes("Tutto pronto"));          // la serata vera ha la pagella ma non il verdetto
});

test("serataCheck: la prova conta controlli e letture e chiude con i giri letti da GitHub", async () => {
  const realFetch = globalThis.fetch;
  const P = { ...S, id: "p", nome: "Prova generale", prova: true };
  const runs = [];
  for (let m = 2; m < 900; m += 10) runs.push({ created_at: new Date(Date.parse(P.inizio) + m * 60e3).toISOString(), conclusion: "success" });
  const world = { calls: [], extra: "", tutti: ["1"], arrivo: [], names: {}, yt: { UUtga: [{ id: "a", titolo: "Old" }] }, runs };
  globalThis.fetch = fakeFetch(world);
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1", YOUTUBE_API_KEY: "k", GH_DISPATCH_TOKEN: "t" };
    const sent = [];
    const tg = async (_e, _c, text) => sent.push(text);
    for (let t = Date.parse(P.inizio) + 60e3; t < Date.parse(P.fine); t += 600e3) await serataCheck(env, tg, t, [P]);
    assert.match(sent[0], /^🧪/);
    const scheda = JSON.parse(env.SUBS.data.get(STATE)).pagella.p;
    assert.equal(scheda.controlli, 90);
    assert.deepEqual(scheda.fonti["half-life.com"], [90, 0]);
    assert.deepEqual(scheda.fonti["YouTube The Game Awards"], [90, 0]);
    await serataCheck(env, tg, Date.parse(P.fine) + 60e3, [P]);
    assert.ok(world.calls.some((u) => u.includes("/actions/workflows/hlx-watch.yml/runs?per_page=100&created=")));
    assert.match(sent.at(-1), /giri completi: 90 partiti, 90 riusciti[\s\S]*Tutto pronto per la serata vera/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("canali di commento: video da controllare, mai un possibile annuncio", () => {
  const canali = COMMENT_CHANNELS.map((c) => ({ ...c, tutti: false, commento: true }));
  const tyler = COMMENT_CHANNELS.find((c) => c.nome === "Tyler McVicker");
  const first = compareSerata({}, { yt: { [tyler.id]: [{ id: "old", titolo: "HLX Files #20" }] } }, canali, during);
  assert.equal(first.videoDaControllare.length, 0);                          // la prima lettura impara
  const next = compareSerata(first.state, { yt: { [tyler.id]: [
    { id: "a", titolo: "Half-Life 3 MASSIVE Leak - HLX Files #21", testo: "" },
    { id: "b", titolo: "My new desk setup", testo: "nothing about games" },
    { id: "c", titolo: "Deadlock is changing", testo: "" },
    { id: "old", titolo: "HLX Files #20" }] } }, canali, during);
  assert.equal(next.annunci.length, 0);                                       // mai un possibile annuncio
  assert.deepEqual(next.videoDaControllare.map((v) => v.url.slice(-1)), ["c", "a"]);
  assert.equal(next.note.length, 0);
  const text = videoText(next.videoDaControllare);
  assert.match(text, /^📺 <b>Video nuovo da controllare<\/b>[\s\S]*Tyler McVicker: <a href="https:\/\/www.youtube.com\/watch\?v=a">Half-Life 3 MASSIVE Leak/);
  assert.match(text, /registro delle previsioni/);
});

test("serataCheck: i canali di commento si leggono sempre con la chiave e mandano un messaggio a parte", async () => {
  const realFetch = globalThis.fetch;
  const gabe = COMMENT_CHANNELS.find((c) => c.nome === "Gabe Follower");
  const pl = uploadsPlaylist(gabe.id);
  const world = { calls: [], extra: "", tutti: [], arrivo: [], names: {}, yt: { [pl]: [{ id: "x1", titolo: "HLX News" }] } };
  globalThis.fetch = fakeFetch(world);
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1", YOUTUBE_API_KEY: "k" };
    const sent = [];
    const tg = async (_e, _c, text) => sent.push(text);
    const day = Date.parse("2026-11-20T12:00:00Z");                             // fuori da ogni serata
    assert.deepEqual(await serataCheck(env, tg, day, [S]), []);
    assert.ok(world.calls.some((u) => u.includes(`playlistId=${pl}`)));
    assert.equal(sent.length, 0);
    world.yt[pl] = [{ id: "x2", titolo: "New Half-Life 3 is Still Real - HLX News" }, { id: "x1", titolo: "HLX News" }];
    assert.deepEqual(await serataCheck(env, tg, day + 600e3, [S]), []);        // nessun possibile annuncio
    assert.equal(sent.length, 1);
    assert.match(sent[0], /Video nuovo da controllare[\s\S]*Gabe Follower[\s\S]*x2/);
    await serataCheck(env, tg, day + 1200e3, [S]);
    assert.equal(sent.length, 1);                                             // una volta sola
  } finally {
    globalThis.fetch = realFetch;
  }
});
