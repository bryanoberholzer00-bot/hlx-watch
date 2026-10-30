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

// Chiamata dal cron di 10 minuti. Ritorna cosa ha fatto (per i log e i test).
export async function dispatchRun(env, sendTelegram, now = Date.now()) {
  if (!env.GH_DISPATCH_TOKEN || !env.SUBS) return { done: false, why: "token non impostato" };
  const list = await fetch(`${API}/runs?per_page=5`, { headers: headers(env) });
  if (list.status === 401 || list.status === 403 || list.status === 404) {
    await tokenProblem(env, sendTelegram, `HTTP ${list.status} leggendo i run`);
    return { done: false, why: `token rifiutato (${list.status})` };
  }
  if (!list.ok) return { done: false, why: `GitHub non risponde (${list.status})` };   // si riprova fra 10 minuti
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
  return { attivo: Boolean(env.GH_DISPATCH_TOKEN) && !error, token: Boolean(env.GH_DISPATCH_TOKEN), errore: error || null };
}
