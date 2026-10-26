// HLX Watch - comandi del bot Telegram, sempre disponibili anche a PC spento.
//
// Il bot di HLX Watch manda avvisi e report dai run di GitHub Actions. Questo Cloudflare Worker riceve
// i messaggi in arrivo (webhook Telegram) e risponde subito ai comandi, leggendo i piccoli file che il
// sito pubblica a ogni run in bot/ (ultime.json, indice.json, stato.json). Non salva nulla.
//
// Segreti (wrangler secret put): BOT_TOKEN, WEBHOOK_SECRET, OWNER_CHAT_ID
// Notifiche di sistema del sito (percorsi /push/...): vedi src/push.js
// Variabili facoltative: SITE_URL (default il sito pubblico), DRY_RUN=1 (risponde nella risposta HTTP: per i test)

import { flushMorning, handlePush } from "./push.js";

const DEFAULT_SITE = "https://bryanoberholzer00-bot.github.io/hlx-watch/";
const REPO = "https://github.com/bryanoberholzer00-bot/hlx-watch";
const MAX_RESULTS = 10;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const dot = (score) => (score >= 100 ? "🔴" : score >= 50 ? "🟠" : "⚪");

async function loadJson(env, name) {
  const site = env.SITE_URL || DEFAULT_SITE;
  const res = await fetch(`${site}bot/${name}`, { cf: { cacheTtl: 300, cacheEverything: true } });
  if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
  return res.json();
}

function line(item) {
  const present = item.t === "present";
  // si taglia prima di convertire in HTML: un'entità spezzata (&quot;) farebbe rifiutare il messaggio
  const label = esc(String(present ? item.x : (item.k || item.x)).slice(0, 110));
  const detail = !present && item.k && item.x && item.x !== item.k ? `\n<i>${esc(String(item.x).slice(0, 110))}</i>` : "";
  const more = item.n > 1 ? ` (+${item.n - 1} file)` : "";
  const link = item.u ? ` <a href="${esc(item.u)}">${esc(item.ul || (present ? "fonte" : "commit"))}</a>` : "";
  const kind = present ? "già presente" : item.t;
  return `${dot(item.s)} <b>[${item.s}]</b> ${esc(item.d)} · ${esc(item.g)} · ${esc(kind)}\n` +
    `<code>${label}</code>${detail}\n${esc(item.f)}${more}${link}`;
}

// Telegram accetta al massimo 4096 caratteri: si aggiungono voci finché c'è spazio.
function fit(header, items, footer, limit = 3900) {
  let body = "";
  let shown = 0;
  for (const item of items) {
    const piece = (shown ? "\n\n" : "") + line(item);
    if ((header + body + piece + footer).length > limit) break;
    body += piece;
    shown += 1;
  }
  return { text: header + body + footer, shown };
}

// ------------------------------------------------------------------ comandi
function welcome(isOwner, site) {
  const commands = [
    "/ultime — le ultime scoperte rilevanti",
    "/cerca <i>testo</i> — cerca nell'archivio (es. /cerca hlx_)",
    "/stato — ultima scansione, numeri, ultima analisi AI",
  ].join("\n");
  if (isOwner) {
    return `<b>Ciao, sei il proprietario di HLX Watch.</b>\n\nQui ricevi gli avvisi immediati quando compare una ` +
      `stringa HLX mai vista e il report delle ultime 24 ore, una volta al giorno in mattinata.\n\n${commands}\n\nDashboard: ${site}`;
  }
  return `<b>Benvenuto in HLX Watch</b>\n\nQuesto bot sorveglia CS2, Dota 2, Deadlock, Half-Life: Alyx e il client Steam ` +
    `alla ricerca di riferimenti a <b>HLX</b>, il presunto nome in codice di Half-Life 3.\n\nGli avvisi automatici ` +
    `arrivano solo al proprietario, ma puoi consultare l'archivio:\n${commands}\n\n• dashboard: ${site}\n• codice: ${REPO}`;
}

async function ultime(env, site) {
  const items = await loadJson(env, "ultime.json");
  if (!items.length) return "Nessuna scoperta rilevante per ora.";
  return fit(`<b>Ultime scoperte rilevanti</b> (punteggio ≥ 50)\n\n`, items.slice(0, MAX_RESULTS),
    `\n\nTutte: ${site}search.html?min=50`).text;
}

async function cerca(env, site, query) {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return "Scrivi cosa cercare, per esempio: <code>/cerca hlx_</code> oppure <code>/cerca thumper</code>";
  const index = await loadJson(env, "indice.json");
  const hits = index.filter((i) => i.k.toLowerCase().includes(q) || i.x.toLowerCase().includes(q) || i.f.toLowerCase().includes(q));
  const full = `${site}search.html?q=${encodeURIComponent(query.trim())}`;
  if (!hits.length) {
    return `Nessun risultato per <code>${esc(query)}</code> tra le voci rilevanti (punteggio ≥ 40).\n\nRicerca completa nell'archivio: ${full}`;
  }
  hits.sort((a, b) => b.s - a.s || (b.d > a.d ? 1 : -1));
  const footer = `\n\nRicerca completa, anche voci minori: ${full}`;
  const draft = fit("", hits.slice(0, MAX_RESULTS), footer, 3800);
  const header = `<b>${hits.length} risultati</b> per <code>${esc(query.slice(0, 60))}</code>` +
    (hits.length > draft.shown ? ` (i ${draft.shown} più rilevanti)` : "") + `\n\n`;
  return header + draft.text;
}

async function stato(env, site) {
  const s = await loadJson(env, "stato.json");
  const a = s.analisi
    ? `${esc(s.analisi.data)}: ${esc(s.analisi.titolo)}${s.analisi.novita_rilevanti ? "" : " (giornata tranquilla)"}`
    : "nessuna ancora";
  const repos = s.repo_scoperti.length ? `\nRepository scoperti e seguiti: ${s.repo_scoperti.map(esc).join(", ")}` : "";
  const health = (s.fonti_in_errore || []).length
    ? `⚠️ Fonti con errori: ${s.fonti_in_errore.map(esc).join(", ")}\n`
    : "✅ Tutte le fonti funzionano\n";
  return `<b>Stato di HLX Watch</b> (ora italiana)\n\n` + health +
    `Ultima scansione: <b>${esc(s.ultima_scansione)}</b>\n` +
    `Ultimo report giornaliero: ${esc(s.report_giornaliero)}\n` +
    `Ultima analisi AI: ${a}\n\n` +
    `Voci in archivio: ${s.voci} (${s.alta} ad alta rilevanza, ${s.gia_presenti} già presenti)\n` +
    `Commit e build analizzati: ${s.commit}, build Steam: ${s.build_steam}, app Steam sospette: ${s.app_sospette}\n` +
    `Giochi seguiti: ${s.giochi.map(esc).join(", ")}${repos}\n\n` +
    `Dashboard: ${site}\nAnalisi AI: ${site}analisi.html`;
}

// ------------------------------------------------------------------ Telegram
async function reply(env, chatId, text) {
  if (env.DRY_RUN === "1") return;
  const res = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) console.log("sendMessage", res.status, await res.text());
}

async function answer(env, text, chatId) {
  const site = env.SITE_URL || DEFAULT_SITE;
  const [head, ...rest] = text.trim().split(/\s+/);
  const command = (head || "").split("@")[0].toLowerCase();
  const args = rest.join(" ");
  const isOwner = String(chatId) === String(env.OWNER_CHAT_ID);
  try {
    switch (command) {
      case "/start":
      case "/help":
      case "/aiuto":
        return welcome(isOwner, site);
      case "/ultime":
        return await ultime(env, site);
      case "/cerca":
        return await cerca(env, site, args);
      case "/stato":
        return await stato(env, site);
      default:
        return command.startsWith("/") ? `Comando sconosciuto. Prova /help.` : null;
    }
  } catch (err) {
    console.log("errore comando", command, String(err));
    return `Non riesco a leggere i dati in questo momento, riprova tra poco.\nDashboard: ${site}`;
  }
}

// ------------------------------------------------------- controllo esterno
// Gira ogni 6 ore (triggers.crons in wrangler.jsonc). Se il sito non viene aggiornato da 12 ore, i run su
// GitHub non stanno partendo: è l'unico guasto che HLX Watch non può segnalare da solo. Senza memoria tra
// un controllo e l'altro, avvisa quando il ritardo entra nelle finestre 12-18 ore e 48-54 ore: due avvisi
// in tutto, non uno ogni 6 ore.
const HOUR = 3600 * 1000;

async function scheduledCheck(env) {
  let ageHours;
  try {
    const s = await loadJson(env, "stato.json");
    ageHours = (Date.now() - Date.parse(s.aggiornato_utc)) / HOUR;
  } catch (err) {
    ageHours = null;
    console.log("controllo programmato: stato.json non leggibile", String(err));
  }
  const site = env.SITE_URL || DEFAULT_SITE;
  let text = null;
  if (ageHours === null || Number.isNaN(ageHours)) {
    text = `⚠️ <b>HLX Watch: il sito non risponde</b>\nNon riesco a leggere lo stato su ${site}. Controlla GitHub Pages e i run su ${REPO}/actions`;
  } else if ((ageHours >= 12 && ageHours < 18) || (ageHours >= 48 && ageHours < 54)) {
    text = `⚠️ <b>HLX Watch è fermo da ${Math.floor(ageHours)} ore</b>\nIl sito non viene aggiornato: i run su GitHub non ` +
      `partono o falliscono prima della fine.\nControlla: ${REPO}/actions\n(Se il repository è rimasto senza attività, ` +
      `GitHub può aver disattivato la pianificazione: si riattiva dalla scheda Actions.)`;
  }
  if (text) {
    console.log("controllo programmato: avviso", ageHours);
    if (env.OWNER_CHAT_ID) await reply(env, env.OWNER_CHAT_ID, text);
  } else if (ageHours >= 12) {
    console.log("controllo programmato: fermo da", ageHours.toFixed(1), "ore, avviso già inviato o non ancora dovuto");
  } else {
    console.log("controllo programmato: tutto ok, aggiornato", ageHours.toFixed(1), "ore fa");
  }
  return text;
}

const BOT_ORIGIN = "https://hlx-watch-bot.bryan-oberholzer00.workers.dev";
const MORNING_CRON = "7 * * * *";

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === MORNING_CRON) {
      // ogni ora: finito l'orario di silenzio manda il riassunto delle notifiche trattenute di notte
      ctx.waitUntil(flushMorning(env, env.SITE_URL || DEFAULT_SITE, BOT_ORIGIN));
    } else {
      ctx.waitUntil(scheduledCheck(env));
    }
  },

  async fetch(request, env) {
    // notifiche di sistema del sito: iscrizioni dal browser e invii dai run su GitHub (src/push.js)
    if (new URL(request.url).pathname.startsWith("/push/")) {
      return handlePush(request, env);
    }
    if (request.method !== "POST") {
      return new Response("HLX Watch bot: ok", { status: 200 });
    }
    // Telegram rimanda il segreto scelto in setWebhook: tutto il resto viene ignorato.
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }
    const message = update.message;
    if (message && message.chat && typeof message.text === "string") {
      const text = await answer(env, message.text, message.chat.id);
      if (text) {
        await reply(env, message.chat.id, text);
        if (env.DRY_RUN === "1") return new Response(text, { status: 200 });
      }
    }
    // Sempre 200: altrimenti Telegram ripete lo stesso aggiornamento all'infinito.
    return new Response("ok", { status: 200 });
  },
};
