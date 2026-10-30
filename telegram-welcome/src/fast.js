// HLX Watch - controllo veloce dell'annuncio, ogni 10 minuti.
//
// I run pianificati su GitHub partono in media ogni 4-5 ore, non ogni 30 minuti: il giorno dell'annuncio l'avviso
// arriverebbe con ore di ritardo. Questo controllo gira nel Worker (i cron di Cloudflare sono puntuali) e guarda
// solo le due fonti che possono annunciare un gioco:
//   - i post nella sezione Annunci di Steam dei giochi Half-Life, di SteamVR, del client Steam e dello Steam Deck
//     (ISteamNews/GetNewsForApp, pubblica, senza chiave);
//   - i repository nuovi dell'organizzazione SteamTracking su GitHub (API pubblica, senza token).
// Se trova un Half-Life nuovo manda subito l'avviso: Telegram al proprietario, notifiche del sito a tutti (anche di
// notte) e una voce in /fast/annuncio.json, che l'app Android legge insieme a bot/ultime.json. Le regole sono le
// stesse di hlxwatch/sources/steam_news.py e del piano annuncio; il giro completo su GitHub poi archivia il post.
// Il KV si scrive solo quando qualcosa cambia (il piano gratuito concede 1000 scritture al giorno).

import { broadcast } from "./push.js";

export const FAST_CRON = "*/10 * * * *";
const FEED = "steam_community_announcements";
const NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/";
const REPOS_API = "https://api.github.com/orgs/SteamTracking/repos?sort=created&direction=desc&per_page=15";
const STATE = "fast:seen";
const ALERTS = "fast:alerts";
const KEEP = 40;
const MAX_AGE_MS = 3 * 24 * 3600 * 1000;
export const PREFIX = "POSSIBILE ANNUNCIO: ";
const HEADING = "HLX Watch: POSSIBILE ANNUNCIO di Valve";

// stessi titoli dei giochi del sito, così l'app riconosce la stessa voce quando arriva dal giro completo
export const APPS = [
  { appid: 70, title: "Half-Life" },
  { appid: 220, title: "Half-Life 2" },
  { appid: 380, title: "Half-Life 2: Episode One" },
  { appid: 420, title: "Half-Life 2: Episode Two" },
  { appid: 546560, title: "Half-Life: Alyx" },
  { appid: 250820, title: "SteamVR" },
  { appid: 593110, title: "Client Steam" },
  { appid: 1675200, title: "Steam Deck" },
];

// ------------------------------------------------------------------ regole (come steam_news.py)
const KNOWN_TITLES = new RegExp(
  "half[\\s-]*life\\s*(?:2\\s*:?\\s*)?(?:[:\\-–]\\s*)?(?:alyx|source|opposing force|blue shift|decay|deathmatch(?: source)?" +
  "|episode (?:one|two|1|2)|lost coast|uplink|25th anniversary|20th anniversary)\\b", "gi");
const NEW_TEXT = "\\bhlx\\b|\\bhl[\\s_-]?3\\b|half[\\s-]*life\\s*(?:3|iii)\\b|episode (?:three|3)\\b";
const NEW_IN_TEXT = new RegExp(NEW_TEXT, "i");
const NEW_IN_TITLE = new RegExp(NEW_TEXT + "|half[\\s-]*life\\s*:\\s*[a-z0-9]", "i");
const BBCODE = /\[\/?[a-z0-9*]+(?:=[^\]]*)?\]/gi;
const HLX_REPO = /hlx|half[\s_-]*life|(?<![a-z0-9])hl[\s_-]?3(?![0-9])/i;

export function clean(text) {
  return String(text || "").replace(BBCODE, " ").replace(/\{STEAM_CLAN_IMAGE\}\S*|https?:\/\/\S+/g, " ")
    .split("\n").map((l) => l.split(/\s+/).filter(Boolean).join(" ")).filter(Boolean).join("\n");
}

export function newHalfLife(title, contents) {
  const t = String(title || "").replace(KNOWN_TITLES, " ");
  const b = String(contents || "").replace(KNOWN_TITLES, " ");
  return NEW_IN_TITLE.test(t) || NEW_IN_TEXT.test(b);
}

export const postUrl = (appid, gid) => `https://store.steampowered.com/news/app/${appid}/view/${gid}`;
const oneLine = (s) => String(s || "").split(/\s+/).filter(Boolean).join(" ");

function romeDay(ms) {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Rome" }).format(new Date(ms));
}

// voce nello stesso formato di bot/ultime.json (vedi hlxwatch/reports/bot_data.py)
export function newsItem(app, post) {
  const title = oneLine(post.title).slice(0, 300);
  const when = Number(post.date || 0) * 1000 || Date.now();
  return {
    s: 200, g: app.title, t: "news", k: "", x: (PREFIX + title).slice(0, 140),
    f: `Annunci di Steam: ${app.title}`, d: romeDay(when), n: 1, u: postUrl(app.appid, post.gid),
    ul: "post su Steam", a: 1, c: new Date(when).toISOString().slice(0, 19) + "Z",
  };
}

function repoItem(repo) {
  const when = Date.parse(repo.created_at) || Date.now();
  return {
    s: 200, g: "SteamTracking (nuovi repository)", t: "new_repo", k: "", x: (PREFIX + `nuovo repository ${repo.full_name}`).slice(0, 140),
    f: repo.full_name, d: romeDay(when), n: 1, u: repo.html_url, ul: "repository", a: 1,
    c: new Date(when).toISOString().slice(0, 19) + "Z",
  };
}

// ------------------------------------------------------------------ controllo
async function fetchNews(appid) {
  const url = `${NEWS_API}?appid=${appid}&count=5&maxlength=3000&feeds=${FEED}`;
  const res = await fetch(url, { headers: { "User-Agent": "hlx-watch-bot" } });
  if (!res.ok) throw new Error(`notizie ${appid}: HTTP ${res.status}`);
  const data = await res.json();
  return (data.appnews && data.appnews.newsitems) || [];
}

async function fetchRepos() {
  const res = await fetch(REPOS_API, { headers: { "User-Agent": "hlx-watch-bot", Accept: "application/vnd.github+json" } });
  if (!res.ok) throw new Error(`repository: HTTP ${res.status}`);    // senza token può capitare un 403: si riprova dopo
  return res.json();
}

// Confronta con quanto già visto. Ritorna le voci nuove da segnalare e lo stato aggiornato (changed = da scrivere).
export function compare(state, news, repos, now = Date.now()) {
  const seen = { apps: { ...(state.apps || {}) }, repos: state.repos ? [...state.repos] : null };
  const alerts = [];
  let changed = false;
  for (const { app, items } of news) {
    const key = String(app.appid);
    const first = !(key in seen.apps);
    const list = first ? [] : [...seen.apps[key]];
    for (const post of [...items].sort((a, b) => Number(a.date) - Number(b.date))) {
      const gid = String(post.gid || "");
      if (!gid || list.includes(gid) || (post.feedname && post.feedname !== FEED)) continue;
      list.push(gid);
      changed = true;
      const fresh = now - Number(post.date || 0) * 1000 <= MAX_AGE_MS;
      if (!first && fresh && newHalfLife(post.title, clean(post.contents))) alerts.push(newsItem(app, post));
    }
    if (first) changed = true;
    seen.apps[key] = list.slice(-KEEP);
  }
  if (repos) {
    const first = seen.repos === null;
    const names = first ? [] : seen.repos;
    for (const repo of repos) {
      const name = String(repo.full_name || "");
      if (!name || names.includes(name)) continue;
      names.push(name);
      changed = true;
      if (!first && HLX_REPO.test(`${repo.name} ${repo.description || ""}`)) alerts.push(repoItem(repo));
    }
    if (first) changed = true;
    seen.repos = names.slice(-KEEP);
  }
  return { alerts, state: seen, changed };
}

function telegramText(alerts, site) {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const lines = [`<b>${HEADING}</b>`, "<i>controllo veloce: il giro completo su GitHub aggiungerà i dettagli</i>", ""];
  for (const a of alerts.slice(0, 5)) {
    lines.push(`🔴 <b>${esc(a.g)}</b>: ${esc(a.x.replace(PREFIX, ""))}`, `<a href="${esc(a.u)}">${esc(a.ul)}</a>`, "");
  }
  lines.push(`Dashboard: ${site}`);
  return lines.join("\n");
}

export async function fastCheck(env, site, origin, sendTelegram) {
  if (!env.SUBS) return null;
  const state = JSON.parse((await env.SUBS.get(STATE)) || "{}");
  const news = [];
  for (const app of APPS) {
    try {
      news.push({ app, items: await fetchNews(app.appid) });
    } catch (err) {
      console.log("controllo veloce:", String(err));       // un'app che non risponde non ferma le altre
    }
  }
  let repos = null;
  try { repos = await fetchRepos(); } catch (err) { console.log("controllo veloce:", String(err)); }
  const result = compare(state, news, repos);
  if (result.changed) await env.SUBS.put(STATE, JSON.stringify(result.state));
  if (!result.alerts.length) return result;

  console.log("controllo veloce: POSSIBILE ANNUNCIO", result.alerts.map((a) => a.x).join(" | "));
  const old = JSON.parse((await env.SUBS.get(ALERTS)) || "[]");
  await env.SUBS.put(ALERTS, JSON.stringify([...result.alerts, ...old].slice(0, 10)));
  if (env.OWNER_CHAT_ID) await sendTelegram(env, env.OWNER_CHAT_ID, telegramText(result.alerts, site));
  const top = result.alerts[0];
  try {
    await broadcast(env, {
      title: HEADING, urgente: true, url: top.u, tag: `hlx-annuncio-${Date.now()}`,
      body: result.alerts.slice(0, 3).map((a) => `🔴 ${a.g}: ${a.x.replace(PREFIX, "")}`.slice(0, 110)).join("\n"),
    }, origin);
  } catch (err) {
    console.log("controllo veloce: notifiche del sito non inviate", String(err));
  }
  return result;
}

// GET /fast/annuncio.json: le voci di annuncio degli ultimi 3 giorni, per l'app Android
export async function fastAlerts(env, now = Date.now()) {
  const list = JSON.parse((env.SUBS && (await env.SUBS.get(ALERTS))) || "[]");
  const recent = list.filter((a) => now - Date.parse(a.c) <= MAX_AGE_MS);
  return new Response(JSON.stringify(recent), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
      "access-control-allow-origin": "*" },
  });
}
