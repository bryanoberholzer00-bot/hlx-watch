// HLX Watch - pulsante "è rumore" (👎) sotto gli avvisi su Telegram.
//
// Gli avvisi mandati dai run su GitHub hanno un pulsante 👎 per ogni voce (callback_data "r:<id della voce>").
// Quando il proprietario lo preme, qui si registra il voto nel KV (rumore:voti, {id: data}); premuto di nuovo lo
// annulla. Il giro su GitHub legge l'elenco da /rumore.json e non manda più avvisi per le voci dello stesso tipo
// (stesso gioco, tipo di modifica, parola chiave e area dei file): vedi hlxwatch/noise.py. Le tracce HLX forti e i
// possibili annunci non si possono silenziare. Solo il proprietario (OWNER_CHAT_ID) può votare.

export const NOISE_KEY = "rumore:voti";
const MAX_VOTES = 300;
const API = (env, method) => `https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`;

export function parseData(data) {
  const m = /^r:(\d{1,12})$/.exec(String(data || ""));
  return m ? m[1] : null;
}

async function readVotes(env) {
  try {
    const v = JSON.parse((await env.SUBS.get(NOISE_KEY)) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

// Registra o annulla il voto per la voce `id`. Ritorna true se ora è segnata come rumore.
export async function toggleVote(env, id, now = Date.now()) {
  const votes = await readVotes(env);
  let on;
  if (votes[id]) {
    delete votes[id];
    on = false;
  } else {
    votes[id] = new Date(now).toISOString();
    on = true;
  }
  const kept = Object.entries(votes).sort((a, b) => a[1].localeCompare(b[1])).slice(-MAX_VOTES);
  await env.SUBS.put(NOISE_KEY, JSON.stringify(Object.fromEntries(kept)));
  return on;
}

// Il pulsante premuto cambia aspetto: "👎 3" (da segnare) <-> "↩️ 3" (segnata, tocca per annullare)
export function updatedKeyboard(markup, data, on) {
  if (!markup || !Array.isArray(markup.inline_keyboard)) return null;
  return {
    inline_keyboard: markup.inline_keyboard.map((row) => row.map((b) => {
      if (b.callback_data !== data) return b;
      const n = String(b.text || "").replace(/^\S+\s*/, "");
      return { ...b, text: `${on ? "↩️" : "👎"} ${n}` };
    })),
  };
}

async function call(env, method, body, fetchFn) {
  if (env.DRY_RUN === "1") return null;
  const res = await fetchFn(API(env, method), {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) console.log(method, res.status, await res.text());
  return res;
}

export async function handleCallback(env, cq, fetchFn = fetch, now = Date.now()) {
  const data = String(cq.data || "");
  const id = parseData(data);
  if (!id || !env.SUBS) {
    await call(env, "answerCallbackQuery", { callback_query_id: cq.id }, fetchFn);
    return null;
  }
  if (String(cq.from && cq.from.id) !== String(env.OWNER_CHAT_ID)) {
    await call(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "Solo il proprietario del bot può votare." },
      fetchFn);
    return null;
  }
  const on = await toggleVote(env, id, now);
  await call(env, "answerCallbackQuery", {
    callback_query_id: cq.id,
    text: on ? "Segnata come rumore: le voci di questo tipo non manderanno più avvisi (si vedono ancora sul sito)."
      : "Annullato: le voci di questo tipo tornano ad avvisare.",
  }, fetchFn);
  const msg = cq.message;
  const keyboard = msg ? updatedKeyboard(msg.reply_markup, data, on) : null;
  if (keyboard) {
    await call(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id, reply_markup: keyboard },
      fetchFn);
  }
  return on;
}

// GET /rumore.json: gli id delle voci segnate (solo numeri, niente dati personali), per il giro su GitHub
export async function noiseList(env) {
  const votes = env.SUBS ? await readVotes(env) : {};
  return new Response(JSON.stringify({ voti: votes }), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
      "access-control-allow-origin": "*" },
  });
}

// Una volta all'ora: il webhook deve ricevere anche i tocchi sui pulsanti (callback_query). Il bot era stato
// registrato solo per i messaggi; si corregge da qui, con il token e il segreto che il Worker ha già.
export async function ensureWebhook(env, fetchFn = fetch) {
  if (!env.BOT_TOKEN || !env.WEBHOOK_SECRET || env.DRY_RUN === "1") return null;
  const res = await fetchFn(API(env, "getWebhookInfo"));
  if (!res.ok) return null;
  const info = (await res.json()).result || {};
  const allowed = info.allowed_updates;
  if (!info.url || !Array.isArray(allowed) || allowed.includes("callback_query")) return false;
  await call(env, "setWebhook", {
    url: info.url, secret_token: env.WEBHOOK_SECRET, allowed_updates: [...new Set([...allowed, "callback_query"])],
  }, fetchFn);
  console.log("webhook: aggiunti i tocchi sui pulsanti (callback_query)");
  return true;
}
