// Test del controllo veloce: node --test telegram-welcome/test
import assert from "node:assert/strict";
import { test } from "node:test";

import { APPS, PREFIX, clean, compare, fastAlerts, fastCheck, fastStatus, fastWatchdog, newHalfLife, newsItem } from "../src/fast.js";

const now = Date.parse("2026-10-30T12:00:00Z");
const post = (gid, title, hoursAgo = 1, contents = "") =>
  ({ gid: String(gid), title, contents, feedname: "steam_community_announcements", date: Math.floor((now - hoursAgo * 3600e3) / 1000) });
const alyx = APPS.find((a) => a.appid === 546560);
const steam = APPS.find((a) => a.appid === 593110);

test("regola dell'annuncio uguale a steam_news.py", () => {
  assert.ok(newHalfLife("Half-Life 3", ""));
  assert.ok(newHalfLife("Announcing Half-Life: Citadel", ""));
  assert.ok(newHalfLife("A new update", "The HLX team says hi"));
  assert.ok(newHalfLife("Half-Life 2: Episode Three", ""));
  for (const t of ["Half-Life: Alyx - October 26, 2026 Update", "Half-Life 2: Episode Two - Update",
    "Half-Life 25th Anniversary Update", "Half-Life 2 - Update", "October 26, 2026 Update", "Half-Life: Source",
    "Half-Life 2: Lost Coast"]) {
    assert.ok(!newHalfLife(t, "Fixed a crash in Half-Life: Alyx when loading saves"), t);
  }
  assert.equal(clean("[h1]Ciao[/h1] [url=https://x.y]link[/url]\n{STEAM_CLAN_IMAGE}/a.png testo"), "Ciao link\ntesto");
});

test("primo giro solo registrato, poi avvisa una volta sola", () => {
  const first = compare({}, [{ app: alyx, items: [post(1, "October 26, 2026 Update", 80)] }], [{ full_name: "SteamTracking/GameTracking-CS2", name: "GameTracking-CS2" }], now);
  assert.equal(first.alerts.length, 0);
  assert.ok(first.changed);

  const news = [{ app: alyx, items: [post(1, "October 26, 2026 Update", 80)] },
    { app: steam, items: [post(5, "Introducing Half-Life 3", 0.2), post(6, "Old Half-Life 3 news", 100)] }];
  const repos = [{ full_name: "SteamTracking/GameTracking-HalfLife3", name: "GameTracking-HalfLife3", html_url: "https://github.com/SteamTracking/GameTracking-HalfLife3", created_at: "2026-10-30T11:55:00Z" },
    { full_name: "SteamTracking/GameTracking-CS2", name: "GameTracking-CS2" }];
  // Steam alla prima lettura: registrato senza avvisi; il repository nuovo invece è segnalato
  const second = compare(first.state, news, repos, now);
  assert.deepEqual(second.alerts.map((a) => a.t), ["new_repo"]);

  news[1].items.push(post(7, "Half-Life 3 is out", 0.1));
  const third = compare(second.state, news, repos, now);
  assert.deepEqual(third.alerts.map((a) => a.x), [PREFIX + "Half-Life 3 is out"]);
  const fourth = compare(third.state, news, repos, now);
  assert.equal(fourth.alerts.length, 0);
  assert.equal(fourth.changed, false);                   // niente scritture nel KV se non cambia niente
});

test("la voce dell'app coincide con quella del giro completo", () => {
  const item = newsItem(alyx, post("123", "Half-Life:   Citadel", 1));
  assert.equal(item.g, "Half-Life: Alyx");
  assert.equal(item.t, "news");
  assert.equal(item.k, "");
  assert.equal(item.x, PREFIX + "Half-Life: Citadel");
  assert.equal(item.u, "https://store.steampowered.com/news/app/546560/view/123");
  assert.equal(item.a, 1);
});

class FakeKV {
  constructor() { this.data = new Map(); this.writes = 0; }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); this.writes += 1; }
  async delete(k) { this.data.delete(k); }
  async list() { return { keys: [], list_complete: true }; }
}

test("fastCheck manda Telegram e scrive /fast/annuncio.json", async () => {
  const realFetch = globalThis.fetch;
  let steamPosts = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes("api.github.com")) return new Response("[]", { status: 200 });
    const appid = Number(new URL(u).searchParams.get("appid"));
    const items = appid === 593110 ? steamPosts : [];
    return new Response(JSON.stringify({ appnews: { appid, newsitems: items } }), { status: 200 });
  };
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
    const sent = [];
    const tg = async (_env, chat, text) => sent.push(text);
    await fastCheck(env, "https://example/", "https://bot", tg);          // primo giro (stato + battito)
    assert.ok(env.SUBS.data.get("fast:ok"));
    const writes = env.SUBS.writes;
    await fastCheck(env, "https://example/", "https://bot", tg);
    assert.equal(env.SUBS.writes, writes);                              // nessuna novità, nessuna scrittura
    steamPosts = [{ gid: "9", title: "Half-Life 3", contents: "", feedname: "steam_community_announcements", date: Math.floor(Date.now() / 1000) }];
    await fastCheck(env, "https://example/", "https://bot", tg);
    assert.equal(sent.length, 1);
    assert.match(sent[0], /POSSIBILE ANNUNCIO/);
    const res = await fastAlerts(env);
    const list = await res.json();
    assert.equal(list.length, 1);
    assert.equal(list[0].g, "Client Steam");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("battito: un avviso se il controllo veloce si ferma, uno quando riparte", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).includes("api.github.com")
    ? new Response("[]", { status: 200 })
    : new Response(JSON.stringify({ appnews: { newsitems: [] } }), { status: 200 });
  try {
    const env = { SUBS: new FakeKV(), OWNER_CHAT_ID: "1" };
    const sent = [];
    const tg = async (_env, chat, text) => sent.push(text);
    const t = Date.now();
    assert.equal(await fastWatchdog(env, tg, t), null);                 // mai partito: niente avviso
    env.SUBS.data.set("fast:ok", new Date(t - 3 * 3600e3).toISOString());
    let st = await (await fastStatus(env, t)).json();
    assert.equal(st.minuti, 180);
    assert.equal(st.fermo, true);
    assert.equal(await fastWatchdog(env, tg, t), 3);
    assert.equal(await fastWatchdog(env, tg, t + 3600e3), null);        // già avvisato
    assert.equal(sent.length, 1);
    assert.match(sent[0], /fermo da 3 ore/);
    await fastCheck(env, "https://example/", "https://bot", tg);        // riparte
    assert.equal(sent.length, 2);
    assert.match(sent[1], /ripartito/);
    assert.equal(env.SUBS.data.has("fast:fermo"), false);
    st = await (await fastStatus(env)).json();
    assert.equal(st.fermo, false);
    const writes = env.SUBS.writes;
    await fastCheck(env, "https://example/", "https://bot", tg);
    assert.equal(env.SUBS.writes, writes);                              // battito al massimo una volta l'ora
  } finally {
    globalThis.fetch = realFetch;
  }
});
