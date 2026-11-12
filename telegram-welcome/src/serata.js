// HLX Watch - la serata di un evento (The Game Awards...) e le pagine di half-life.com.
//
// Le serate sono in hlxwatch/serate.json, lo stesso file che usa il sito. Dall'inizio alla fine di una serata:
//   - il giro completo su GitHub riparte ogni 10 minuti a tutte le ore (src/dispatch.js);
//   - a ogni controllo veloce si legge la pagina di Valve su Steam (la lista che usa store.steampowered.com/developer/
//     valve, come hlxwatch/sources/steam_valve.py): un prodotto nuovo con Half-Life o HLX nel nome è un possibile
//     annuncio, un altro prodotto nuovo è un messaggio al proprietario;
//   - con la chiave YOUTUBE_API_KEY (API ufficiale YouTube Data, gratuita) si leggono i video nuovi dei canali
//     dell'evento: un titolo con un Half-Life nuovo è un possibile annuncio, uno con "Valve" un messaggio. Il feed
//     pubblico di YouTube non si usa: il robots.txt di YouTube lo vieta;
//   - all'inizio e alla fine il proprietario riceve un messaggio (alla fine: cosa è comparso, o niente, e la pagella:
//     controlli fatti, fonti lette e giri completi partiti, letti da GitHub).
// Una serata con "prova": true è la prova generale: tutto come in quella vera (avvisi veri compresi), ma i messaggi
// dicono che è una prova, la pagella dice se si è pronti per la serata vera e il sito non la mostra.
// Sempre, a ogni controllo veloce (una richiesta di 20 KB): le pagine dei giochi su half-life.com (/en/alyx,
// /en/halflife2...). Una pagina nuova di primo livello è un possibile annuncio: Alyx è comparsa lì il giorno
// dell'annuncio. Con la chiave, anche i video nuovi del canale YouTube di Valve e dei canali di chi parla di HLX
// (COMMENT_CHANNELS: solo un messaggio "da controllare" per il registro delle previsioni, mai un annuncio).
// Ogni lettura parte da sola: una fonte che non risponde non ferma le altre. Il KV si scrive solo quando cambia qualcosa.

import SERATE from "../../hlxwatch/serate.json" with { type: "json" };
import { PREFIX, newHalfLife } from "./fast.js";

export const LIST = SERATE.serate || [];
export const STATE = "serata:stato";
const HL_HOME = "https://www.half-life.com/en/home/";
const VALVE_PAGE = "https://store.steampowered.com/developer/valve/ajaxgetfilteredrecommendations/";
const DETAILS = "https://store.steampowered.com/api/appdetails";
const YT_API = "https://www.googleapis.com/youtube/v3/playlistItems";
const RUNS_API = "https://api.github.com/repos/bryanoberholzer00-bot/hlx-watch/actions/workflows/hlx-watch.yml/runs";
const STEP_MS = 600000;                 // un controllo veloce e un giro completo ogni 10 minuti
const MIN_RATE = 0.9;                   // pagella: almeno il 90% dei controlli e delle letture...
const MAX_GAP_MIN = 25;                 // ...e mai più di 25 minuti senza un giro completo
export const VALVE_CHANNEL = { id: "UCg0FSqPeiGD_lIiPaaAehQg", nome: "Valve" };
// Canali di chi parla di HLX (quelli del registro delle previsioni, hlxwatch/previsioni.json). Un loro video nuovo su
// Half-Life, HLX o Valve è un messaggio "da controllare": le affermazioni vanno messe nel registro. MAI un possibile
// annuncio: i loro titoli ("Half-Life 3 MASSIVE Leak...") lo farebbero scattare a ogni video.
export const COMMENT_CHANNELS = [
  { id: "UCL5RKbiAPqSC_mUIjCpx3xg", nome: "Tyler McVicker" },
  { id: "UCqpWX1aVv2Nnsp8sxuLUIFg", nome: "Gabe Follower" },
  { id: "UCMXYdca-8R-ovf079rJDePA", nome: "GAMERANGE" },
];
const ABOUT_VALVE = /half[\s_-]*life|(?<![a-z0-9])hlx(?![a-z0-9])|(?<![a-z0-9])hl[\s_-]?3(?![0-9])|\bvalve\b|deadlock|steam (?:frame|machine|deck|controller)/i;
const HLX_NAME = /hlx|half[\s_-]*life|(?<![a-z0-9])hl[\s_-]?3(?![0-9])/i;
const HEADERS = { "User-Agent": "Mozilla/5.0 (compatible; hlx-watch-bot; +https://github.com/bryanoberholzer00-bot/hlx-watch)",
  "Accept-Language": "en-US" };
const KEEP = 60;
const DAY_MS = 86400000;

// ------------------------------------------------------------------ quando
export function activeSerata(now = Date.now(), list = LIST) {
  return list.find((s) => Date.parse(s.inizio) <= now && now < Date.parse(s.fine)) || null;
}

// La serata finita da meno di un giorno (per il messaggio di chiusura)
export function endedSerata(now = Date.now(), list = LIST) {
  return list.find((s) => Date.parse(s.fine) <= now && now < Date.parse(s.fine) + DAY_MS) || null;
}

export function romeTime(iso) {
  return new Intl.DateTimeFormat("it-IT", { timeZone: "Europe/Rome", hour: "2-digit", minute: "2-digit" })
    .format(new Date(iso)).replace(/^0/, "");
}

function romeDay(ms) {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Rome" }).format(new Date(ms));
}

// ------------------------------------------------------------------ letture (pure)
// Le pagine dei giochi su half-life.com: "alyx", "halflife2", "halflife2/20th"... (senza home e lingue)
export function halfLifePaths(html) {
  const out = new Set();
  const rx = /href="(?:https?:\/\/www\.half-life\.com)?\/en\/([a-z0-9][a-z0-9/_-]*)"/gi;
  for (const m of String(html || "").matchAll(rx)) {
    const path = m[1].replace(/\/+$/, "").toLowerCase();
    if (path && path !== "home" && !path.startsWith("home/")) out.add(path);
  }
  return [...out].sort();
}

// Una pagina nuova di primo livello (un gioco) è un possibile annuncio; un anniversario (halflife25, halflife2/20th)
// o una pagina dentro un gioco già noto no.
export function isGamePage(path) {
  return !path.includes("/") && !/\d{2}(th)?$/.test(path);
}

export function valveList(data) {
  if (!data || !data.success) return null;
  const ids = [...new Set([...String(data.results_html || "").matchAll(/data-ds-appid=\\?"(\d+)/g)].map((m) => m[1]))];
  return { dichiarati: Number(data.total_count || 0), app: ids };
}

export function uploadsPlaylist(channelId) {
  return "UU" + String(channelId).slice(2);
}

export function videos(data) {
  return ((data && data.items) || []).map((it) => {
    const s = it.snippet || {};
    return { id: String((s.resourceId || {}).videoId || ""), titolo: String(s.title || ""),
      testo: String(s.description || "").slice(0, 2000), quando: String(s.publishedAt || "") };
  }).filter((v) => v.id);
}

// ------------------------------------------------------------------ voci (stesso formato di bot/ultime.json)
function item(g, t, text, f, url, ul, now) {
  return { s: 200, g, t, k: "", x: (PREFIX + text).slice(0, 140), f, d: romeDay(now), n: 1, u: url, ul, a: 1,
    c: new Date(now).toISOString().slice(0, 19) + "Z" };
}

// Confronta le letture con quanto già visto. Una lista mai vista (null nello stato) si impara senza avvisi.
// readings: { pagine: [..]|null, valve: { tutti, arrivo }|null, yt: { canale: [video] } }, canali: [{ id, nome, tutti }]
// Ritorna { annunci, note, nuoveApp, state, changed }: le app nuove vanno poi guardate con appdetails (classifyApp).
export function compareSerata(state, readings, canali = [], now = Date.now()) {
  const st = { ...state, yt: { ...(state.yt || {}) } };
  const annunci = [], note = [], nuoveApp = [], videoDaControllare = [];
  let changed = false;
  if (readings.pagine && readings.pagine.length) {
    const known = st.pagine || null;
    for (const p of readings.pagine) {
      if (!known || known.includes(p)) continue;
      const url = `https://www.half-life.com/en/${p}`;
      if (isGamePage(p)) {
        annunci.push(item("half-life.com", "web", `pagina nuova su half-life.com: /en/${p}`, `half-life.com/en/${p}`, url,
          "la pagina", now));
      } else {
        note.push(`Pagina nuova su half-life.com: <a href="${url}">/en/${p}</a>`);
      }
    }
    const merged = [...new Set([...(known || []), ...readings.pagine])].sort();
    if (!known || merged.length !== known.length) { st.pagine = merged; changed = true; }
  }
  if (readings.valve) {
    const known = st.valve || null;
    const all = [...new Set([...readings.valve.tutti.app, ...readings.valve.arrivo.app])];
    if (known) for (const id of all) if (!known.app.includes(id)) nuoveApp.push(id);
    const conti = { tutti: readings.valve.tutti.dichiarati, arrivo: readings.valve.arrivo.dichiarati };
    if (known && conti.arrivo > known.arrivo) {
      note.push(`I prodotti di Valve in arrivo su Steam passano da ${known.arrivo} a ${conti.arrivo}.`);
    }
    const apps = [...new Set([...((known && known.app) || []), ...all])];
    if (!known || apps.length !== known.app.length || conti.arrivo !== known.arrivo || conti.tutti !== known.tutti) {
      st.valve = { app: apps, ...conti };
      changed = true;
    }
  }
  for (const ch of canali) {
    const list = (readings.yt || {})[ch.id];
    if (!list) continue;
    const known = st.yt[ch.id] || null;
    for (const v of [...list].reverse()) {
      if (known && known.includes(v.id)) continue;
      if (!known) continue;
      const url = `https://www.youtube.com/watch?v=${v.id}`;
      if (ch.commento) {                       // canale di commento: solo "da controllare", mai un annuncio
        if (ABOUT_VALVE.test(`${v.titolo} ${v.testo}`)) videoDaControllare.push({ canale: ch.nome, titolo: v.titolo, url });
      } else if (newHalfLife(v.titolo, v.testo)) {
        annunci.push(item(`YouTube: ${ch.nome}`, "video", `video nuovo: ${v.titolo}`, `youtube.com/${ch.nome}`, url, "il video", now));
      } else if (ch.tutti || /\bvalve\b/i.test(v.titolo)) {
        note.push(`Video nuovo sul canale ${ch.nome}: <a href="${url}">${esc(v.titolo)}</a>`);
      }
    }
    const ids = [...new Set([...list.map((v) => v.id), ...(known || [])])].slice(0, KEEP);
    if (!known || ids.some((id) => !known.includes(id))) { st.yt[ch.id] = ids; changed = true; }
  }
  return { annunci, note, nuoveApp, videoDaControllare, state: st, changed };
}

// Un prodotto nuovo nella pagina di Valve: possibile annuncio con un Half-Life nel nome, altrimenti un messaggio
export function classifyApp(appid, info, now = Date.now()) {
  const name = String((info && info.name) || `app ${appid}`);
  const kind = String((info && info.type) || "");
  const url = `https://store.steampowered.com/app/${appid}/`;
  if (HLX_NAME.test(name)) {
    return { annuncio: item("Valve su Steam", "store", `nuovo prodotto di Valve su Steam: ${name}`, `app ${appid}`, url,
      "pagina su Steam", now) };
  }
  return { nota: `Nuovo prodotto nella pagina di Valve su Steam: <a href="${url}">${esc(name)}</a>${kind ? ` (${esc(kind)})` : ""}` };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ------------------------------------------------------------------ pagella
// I giri completi della serata, dalla lista di GitHub: quanti sono partiti, come sono finiti e il buco più lungo
// senza un giro (dall'inizio al primo, fra un giro e l'altro, dall'ultimo alla fine).
export function runsSummary(runs, s) {
  const from = Date.parse(s.inizio), to = Date.parse(s.fine);
  const times = [];
  let riusciti = 0, falliti = 0, altri = 0;
  for (const r of runs || []) {
    const t = Date.parse(r.created_at);
    if (!(t >= from && t < to)) continue;
    times.push(t);
    if (r.conclusion === "success") riusciti += 1;
    else if (r.conclusion === "failure" || r.conclusion === "timed_out") falliti += 1;
    else altri += 1;                                  // ancora in corso, annullati, saltati
  }
  times.sort((a, b) => a - b);
  const marks = [from, ...times, to];
  let gap = 0;
  for (let i = 1; i < marks.length; i++) gap = Math.max(gap, marks[i] - marks[i - 1]);
  return { avviati: times.length, riusciti, falliti, altri, buco: Math.round(gap / 60000) };
}

// Righe della pagella e problemi trovati (vuoto = tutto a posto)
export function pagella(s, scheda, giri) {
  const righe = [], problemi = [];
  const attesi = Math.floor((Date.parse(s.fine) - Date.parse(s.inizio)) / STEP_MS);
  const fatti = (scheda && scheda.controlli) || 0;
  const okC = fatti >= attesi * MIN_RATE;
  righe.push(`${okC ? "✅" : "❌"} controlli veloci: ${fatti} su ${attesi} attesi`);
  if (!okC) problemi.push("controlli veloci saltati (il cron di Cloudflare non è partito sempre)");
  for (const [nome, [ok, ko]] of Object.entries((scheda && scheda.fonti) || {})) {
    const buona = ok >= (ok + ko) * MIN_RATE;
    righe.push(`${buona ? "✅" : "❌"} ${esc(nome)}: letta ${ok} volte su ${ok + ko}`);
    if (!buona) problemi.push(`${esc(nome)} non risponde bene`);
  }
  if (!giri) {
    righe.push("❔ giri completi: GitHub non ha risposto, non so quanti ne sono partiti");
    problemi.push("giri completi non verificati");
  } else {
    const okG = giri.falliti === 0 && giri.buco <= MAX_GAP_MIN && giri.avviati > 0;
    righe.push(`${okG ? "✅" : "❌"} giri completi: ${giri.avviati} partiti, ${giri.riusciti} riusciti` +
      (giri.falliti ? `, ${giri.falliti} falliti` : "") + `; il buco più lungo senza un giro: ${giri.buco} minuti`);
    if (giri.falliti) problemi.push(`${giri.falliti} giri completi falliti (guarda i log su GitHub)`);
    if (giri.buco > MAX_GAP_MIN || !giri.avviati) problemi.push("giri completi non partiti ogni 10 minuti");
  }
  return { righe, problemi };
}

// ------------------------------------------------------------------ messaggi
// Video nuovi dei canali di commento: da guardare e, se fanno affermazioni su HLX, da mettere nel registro
export function videoText(videos) {
  return "📺 <b>Video nuovo da controllare</b>\n" +
    videos.slice(0, 5).map((v) => `• ${esc(v.canale)}: <a href="${v.url}">${esc(v.titolo)}</a>`).join("\n") +
    "\nSe fa affermazioni su HLX, vanno nel registro delle previsioni: lì si vede poi se i file le confermano.";
}

export function openingText(s) {
  const what = "i post di Steam, la pagina di Valve su Steam, half-life.com, i video su YouTube e i repository nuovi, e " +
    "riavvia il giro completo ogni 10 minuti";
  if (s.prova) {
    return `🧪 <b>${esc(s.nome)}</b>\n` +
      `Da adesso fino alle ${romeTime(s.fine)} di domattina HLX Watch fa tutto come nella serata vera: controlla ogni ` +
      `10 minuti ${what}. Alla fine ti mando la pagella: quanti controlli sono riusciti, quali fonti hanno risposto e quanti ` +
      "giri sono partiti. Se nel frattempo compare davvero Half-Life, l'avviso è vero, non fa parte della prova.";
  }
  return `🏆 <b>Stasera ${esc(s.nome)}</b>\n` +
    `Lo spettacolo inizia alle ${romeTime(s.spettacolo)} (ora italiana). Fino alle ${romeTime(s.fine)} HLX Watch ` +
    `controlla ogni 10 minuti ${what}. Se compare Half-Life ti avviso subito, anche di notte.`;
}

export function closingText(s, trovate, scheda = null, giri = null) {
  const voto = pagella(s, scheda, giri);
  const head = s.prova ? `🧪 <b>${esc(s.nome)}: finita</b>\n` : `🏆 <b>${esc(s.nome)}: la serata è finita</b>\n`;
  let body;
  if (!trovate.length) {
    body = "Nessuna traccia di Half-Life: niente post, niente pagine nuove, niente prodotti nuovi con quel nome." +
      (s.previsione && !s.prova ? " Il registro delle previsioni segnerà da solo la previsione «niente annuncio» quando passa la sua scadenza." : "");
  } else {
    body = "Durante la serata è comparso:\n" + trovate.slice(0, 8).map((t) => `• ${t}`).join("\n");
  }
  let esito = "\n\n<b>Pagella</b>\n" + voto.righe.join("\n");
  if (s.prova) {
    esito += voto.problemi.length
      ? "\n\n⚠️ <b>Da sistemare prima della serata vera:</b>\n" + voto.problemi.map((p) => `• ${p}`).join("\n")
      : "\n\n👍 <b>Tutto pronto per la serata vera.</b>";
  }
  return head + body + esito;
}

// ------------------------------------------------------------------ rete
async function getJson(url) {
  const res = await fetch(url, { headers: HEADERS });
  if (!res.ok) throw new Error(`${new URL(url).host}: HTTP ${res.status}`);
  return res.json();
}

async function readPages() {
  const res = await fetch(HL_HOME, { headers: HEADERS });
  if (!res.ok) throw new Error(`half-life.com: HTTP ${res.status}`);
  return halfLifePaths(await res.text());
}

async function readValve() {
  const q = (sort) => `${VALVE_PAGE}?query=&start=0&count=100&tagids=&app_types=&curations=&reset=false&cc=us&l=english` +
    (sort ? `&sort=${sort}` : "");
  const tutti = valveList(await getJson(q()));
  const arrivo = valveList(await getJson(q("upcoming")));
  return tutti && arrivo ? { tutti, arrivo } : null;
}

async function appInfo(appid) {
  try {
    const d = await getJson(`${DETAILS}?appids=${appid}&filters=basic&cc=us&l=english`);
    return ((d || {})[appid] || {}).data || null;
  } catch {
    return null;
  }
}

async function readChannel(channelId, key) {
  const url = `${YT_API}?part=snippet&maxResults=10&playlistId=${uploadsPlaylist(channelId)}&key=${encodeURIComponent(key)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`YouTube ${channelId}: HTTP ${res.status}`);
  return videos(await res.json());
}

// I giri completi partiti durante la serata (una sola lettura, alla chiusura; serve il token dei giri)
async function readRuns(env, s) {
  if (!env.GH_DISPATCH_TOKEN) return null;
  const res = await fetch(`${RUNS_API}?per_page=100&created=${s.inizio}..${s.fine}`, { headers: {
    Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hlx-watch-bot",
    Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}` } });
  if (!res.ok) throw new Error(`GitHub: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data.workflow_runs) ? runsSummary(data.workflow_runs, s) : null;
}

async function attempt(label, fn) {
  try { return await fn(); } catch (err) { console.log(`serata: ${label} non letto:`, String(err)); return null; }
}

// ------------------------------------------------------------------ controllo
// Chiamata dal controllo veloce ogni 10 minuti. Manda i messaggi della serata e ritorna i possibili annunci
// (nello stesso formato delle voci del controllo veloce, che li manda su Telegram, sul sito e nell'app).
export async function serataCheck(env, sendTelegram, now = Date.now(), list = LIST) {
  if (!env.SUBS) return [];
  let state;
  try { state = JSON.parse((await env.SUBS.get(STATE)) || "{}"); } catch { state = {}; }
  state = { ...state, aperte: [...(state.aperte || [])], chiuse: [...(state.chiuse || [])], trovate: { ...(state.trovate || {}) },
    pagella: { ...(state.pagella || {}) } };
  let changed = false;
  const tell = async (text) => { if (env.OWNER_CHAT_ID) await sendTelegram(env, env.OWNER_CHAT_ID, text); };
  const s = activeSerata(now, list);
  if (s && !state.aperte.includes(s.id)) {
    state.aperte.push(s.id);
    state.valve = null;                                 // la pagina di Valve si impara da capo all'inizio della serata
    for (const ch of s.youtube || []) delete (state.yt || {})[ch.id];
    changed = true;
    await tell(openingText(s));
  }
  // pagella della serata: ogni controllo e ogni lettura riuscita o no (una scrittura ogni 10 minuti, solo di sera)
  let scheda = null;
  if (s) {
    const old = state.pagella[s.id] || {};
    scheda = { controlli: (old.controlli || 0) + 1, fonti: Object.fromEntries(Object.entries(old.fonti || {}).map(([k, v]) => [k, [...v]])) };
    state.pagella[s.id] = scheda;
    changed = true;
  }
  const read = async (label, fn) => {
    const v = await attempt(label, fn);
    if (scheda) {
      const f = scheda.fonti[label] || (scheda.fonti[label] = [0, 0]);
      f[v ? 0 : 1] += 1;
    }
    return v;
  };
  const canali = [];
  if (env.YOUTUBE_API_KEY) {
    canali.push({ ...VALVE_CHANNEL, tutti: true });
    if (s) canali.push(...(s.youtube || []).map((c) => ({ ...c, tutti: false })));
    canali.push(...COMMENT_CHANNELS.map((c) => ({ ...c, tutti: false, commento: true })));
  }
  const readings = { pagine: await read("half-life.com", readPages), valve: null, yt: {} };
  if (s) readings.valve = await read("pagina di Valve su Steam", readValve);
  for (const ch of canali) {
    const v = await read(`YouTube ${ch.nome}`, () => readChannel(ch.id, env.YOUTUBE_API_KEY));
    if (v) readings.yt[ch.id] = v;
  }
  const res = compareSerata(state, readings, canali, now);
  state = res.state;
  changed ||= res.changed;
  const annunci = [...res.annunci];
  const note = [...res.note];
  for (const appid of res.nuoveApp) {
    const c = classifyApp(appid, await appInfo(appid), now);
    if (c.annuncio) annunci.push(c.annuncio); else note.push(c.nota);
  }
  if (s && (annunci.length || note.length)) {
    state.trovate[s.id] = [...(state.trovate[s.id] || []), ...annunci.map((a) => esc(a.x.replace(PREFIX, ""))), ...note].slice(-20);
    changed = true;
  }
  if (note.length) await tell(`${s ? "🏆" : "🌐"} <b>HLX Watch${s ? `: ${esc(s.nome)}` : ""}</b>\n` + note.join("\n"));
  if (res.videoDaControllare.length) await tell(videoText(res.videoDaControllare));
  const ended = endedSerata(now, list);
  if (ended && state.aperte.includes(ended.id) && !state.chiuse.includes(ended.id)) {
    state.chiuse.push(ended.id);
    changed = true;
    const giri = await attempt("giri completi su GitHub", () => readRuns(env, ended));
    await tell(closingText(ended, state.trovate[ended.id] || [], state.pagella[ended.id] || null, giri));
  }
  if (changed) await env.SUBS.put(STATE, JSON.stringify(state));
  return annunci;
}

// Per /fast/stato.json e per l'avvio dei giri: la serata in corso, se c'è
export function serataStatus(now = Date.now(), list = LIST) {
  const s = activeSerata(now, list);
  return s ? { id: s.id, nome: s.nome, spettacolo: s.spettacolo, fine: s.fine } : null;
}
