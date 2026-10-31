// HLX Watch - avvio del giro completo su GitHub ogni 30 minuti.
//
// I run pianificati di GitHub ("schedule") partono quando GitHub ha posto: in pratica ogni 4-5 ore. Un run avviato
// a mano ("workflow_dispatch") parte invece subito. Il bot, che gira puntuale ogni 10 minuti, guarda l'ultimo run
// del workflow e, se è finito da almeno DISPATCH_EVERY_MS, ne avvia uno nuovo: lo stesso giro di un run pianificato
// (tutti gli input ai valori predefiniti). I run pianificati restano attivi come riserva.
//
// Serve un token GitHub "fine-grained" limitato a questo repository con il solo permesso Actions: read and write,
// salvato come segreto del Worker: wrangler secret put GH_DISPATCH_TOKEN. Senza token il modulo non fa niente.
// Se GitHub rifiuta il token (scaduto o revocato) il proprietario riceve un solo avviso; un altro quando riparte.

export const REPO = "bryanoberholzer00-bot/hlx-watch";
export const WORKFLOW = "hlx-watch.yml";
const BRANCH = "main";
export const DISPATCH_EVERY_MS = 28 * 60 * 1000;     // con il controllo ogni 10 minuti: un run ogni 30 minuti
const ERROR_KEY = "dispatch:errore";
// scadenza del token: GitHub la manda in ogni risposta (intestazione github-authentication-token-expiration)
const EXPIRY_KEY = "dispatch:scadenza";
const WARNED_KEY = "dispatch:scadenza_avvisata";
const EXPIRY_HEADER = "github-authentication-token-expiration";
const API = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}`;
const BUSY = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

function headers(env) {
  return {
    Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hlx-watch-bot",
    Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
  };
}

// Decide se avviare un run, guardando gli ultimi del workflow (pianificati, a mano o avviati dal bot).
export function shouldDispatch(runs, now = Date.now()) {
  if (runs.some((r) => BUSY.has(r.status))) return { go: false, why: "un run è già in corso o in coda" };
  const last = Math.max(0, ...runs.map((r) => Date.parse(r.created_at) || 0));
  if (now - last < DISPATCH_EVERY_MS) return { go: false, why: "ultimo run partito da poco" };
  return { go: true, why: "" };
}

async function tokenProblem(env, sendTelegram, detail) {
  if (await env.SUBS.get(ERROR_KEY)) return;                     // già avvisato
  await env.SUBS.put(ERROR_KEY, detail.slice(0, 300));
  if (env.OWNER_CHAT_ID) {
    await sendTelegram(env, env.OWNER_CHAT_ID,
      "⚠️ <b>HLX Watch: il bot non riesce più ad avviare il giro completo</b>\n" +
      `GitHub risponde: ${detail.replace(/[&<>]/g, " ")}\n` +
      "Di solito il token GH_DISPATCH_TOKEN è scaduto: creane uno nuovo (solo questo repository, permesso Actions: read " +
      "and write) e salvalo con <code>npx wrangler secret put GH_DISPATCH_TOKEN</code>. Intanto i run pianificati " +
      "di GitHub continuano, ogni 4-5 ore.");
  }
}

async function recovered(env, sendTelegram) {
  if (!(await env.SUBS.get(ERROR_KEY))) return;
  await env.SUBS.delete(ERROR_KEY);
  if (env.OWNER_CHAT_ID) {
    await sendTelegram(env, env.OWNER_CHAT_ID, "✅ <b>HLX Watch: il bot avvia di nuovo il giro completo ogni 30 minuti</b>");
  }
}

// "2026-11-29 12:00:00 UTC" (o con "-0700") in millisecondi; NaN se non si legge
export function parseExpiry(value) {
  const s = String(value || "").trim();
  if (!s) return NaN;
  const iso = s.replace(" UTC", "Z").replace(/ ([+-]\d{2})(\d{2})$/, "$1:$2").replace(" ", "T");
  return Date.parse(iso);
}

// Salva la data di scadenza (solo se cambia: il KV gratuito concede poche scritture) e avvisa 7 giorni e 1 giorno
// prima, una volta per soglia. Un token rinnovato ha un'altra data: gli avvisi ripartono da capo.
export async function checkExpiry(env, header, sendTelegram, now = Date.now()) {
  const ms = parseExpiry(header);
  if (Number.isNaN(ms)) return null;
  const day = new Date(ms).toISOString().slice(0, 10);
  if ((await env.SUBS.get(EXPIRY_KEY)) !== day) await env.SUBS.put(EXPIRY_KEY, day);
  const days = Math.floor((ms - now) / 86400000);
  const stage = days <= 1 ? "1" : days <= 7 ? "7" : null;
  if (!stage) return days;
  const warned = await env.SUBS.get(WARNED_KEY);
  if (warned === `${day}:${stage}` || (stage === "7" && warned === `${day}:1`)) return days;
  await env.SUBS.put(WARNED_KEY, `${day}:${stage}`);
  if (env.OWNER_CHAT_ID) {
    const when = days <= 0 ? "oggi" : days === 1 ? "domani" : `tra ${days} giorni`;
    const [y, m, d] = day.split("-");
    await sendTelegram(env, env.OWNER_CHAT_ID,
      `⏳ <b>HLX Watch: il token del giro ogni 30 minuti scade ${when} (${d}/${m}/${y})</b>\n` +
      "Rinnovalo prima, così il giro non si ferma:\n" +
      "1. GitHub → Settings → Developer settings → Fine-grained tokens → <b>hlx-watch-bot</b> → <b>Regenerate token</b>\n" +
      "2. Nella cartella telegram-welcome: <code>npx wrangler secret put GH_DISPATCH_TOKEN</code> e incolla il token nuovo.\n" +
      "Se scade, restano comunque i run pianificati di GitHub (ogni 4-5 ore).");
  }
  return days;
}

// Chiamata dal cron di 10 minuti. Ritorna cosa ha fatto (per i log e i test).
export async function dispatchRun(env, sendTelegram, now = Date.now()) {
  if (!env.GH_DISPATCH_TOKEN || !env.SUBS) return { done: false, why: "token non impostato" };
  const list = await fetch(`${API}/runs?per_page=5`, { headers: headers(env) });
  if (list.status === 401 || list.status === 403 || list.status === 404) {
    await tokenProblem(env, sendTelegram, `HTTP ${list.status} leggendo i run`);
    return { done: false, why: `token rifiutato (${list.status})` };
  }
  if (!list.ok) return { done: false, why: `GitHub non risponde (${list.status})` };   // si riprova fra 10 minuti
  await checkExpiry(env, list.headers.get(EXPIRY_HEADER), sendTelegram, now);
  const runs = ((await list.json()).workflow_runs || []);
  const decision = shouldDispatch(runs, now);
  if (!decision.go) {
    await recovered(env, sendTelegram);
    return { done: false, why: decision.why };
  }
  const res = await fetch(`${API}/dispatches`, {
    method: "POST", headers: { ...headers(env), "content-type": "application/json" },
    body: JSON.stringify({ ref: BRANCH }),
  });
  if (res.status === 204) {
    await recovered(env, sendTelegram);
    console.log("giro completo avviato su GitHub");
    return { done: true, why: "" };
  }
  if (res.status === 401 || res.status === 403 || res.status === 404) {
    await tokenProblem(env, sendTelegram, `HTTP ${res.status} avviando il run`);
  }
  return { done: false, why: `avvio rifiutato (${res.status})` };
}

// Per /fast/stato.json: il sito mostra se l'avvio ogni 30 minuti è attivo
export async function dispatchStatus(env) {
  const error = env.SUBS ? await env.SUBS.get(ERROR_KEY) : null;
  const expiry = env.SUBS ? await env.SUBS.get(EXPIRY_KEY) : null;
  return { attivo: Boolean(env.GH_DISPATCH_TOKEN) && !error, token: Boolean(env.GH_DISPATCH_TOKEN), errore: error || null,
    scadenza: env.GH_DISPATCH_TOKEN ? expiry || null : null };
}
