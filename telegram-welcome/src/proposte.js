// HLX Watch - pulsanti ✅ Applica / ❌ No sotto le proposte della revisione settimanale.
//
// Il lunedì il giro su GitHub manda su Telegram al massimo 3 correzioni proposte dall'AI (hlxwatch/revisione.py),
// ognuna con due pulsanti: callback_data "p:<id della proposta>:s" (applica) o ":n" (no). Qui si registra la scelta
// nel KV (revisione:decisioni, {id: {d, at}}); premere l'altro pulsante cambia idea finché il giro non l'ha applicata.
// Il giro su GitHub legge le scelte da /proposte.json e applica quelle approvate. Solo il proprietario può scegliere.

export const DECISIONS_KEY = "revisione:decisioni";
const MAX_DECISIONS = 200;
const API = (env, method) => `https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`;

export function parseProposal(data) {
  const m = /^p:(p\d{6,8}\d{1,2}):([sn])$/.exec(String(data || ""));
  return m ? { id: m[1], d: m[2] } : null;
}

async function readDecisions(env) {
  try {
    const v = JSON.parse((await env.SUBS.get(DECISIONS_KEY)) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

export async function saveDecision(env, id, d, now = Date.now()) {
  const all = await readDecisions(env);
  all[id] = { d, at: new Date(now).toISOString() };
  const kept = Object.entries(all).sort((a, b) => a[1].at.localeCompare(b[1].at)).slice(-MAX_DECISIONS);
  await env.SUBS.put(DECISIONS_KEY, JSON.stringify(Object.fromEntries(kept)));
}

// La coppia di pulsanti della proposta scelta mostra la scelta; l'altro pulsante resta per cambiare idea
export function markedKeyboard(markup, id, d) {
  if (!markup || !Array.isArray(markup.inline_keyboard)) return null;
  return {
    inline_keyboard: markup.inline_keyboard.map((row) => row.map((b) => {
      const p = parseProposal(b.callback_data);
      if (!p || p.id !== id) return b;
      const n = String(b.text || "").replace(/^.*?(\d+)\s*$/, "$1");
      if (p.d === "s") return { ...b, text: d === "s" ? `✅ Approvata ${n}` : `✅ Applica ${n}` };
      return { ...b, text: d === "n" ? `❌ Scartata ${n}` : `❌ No ${n}` };
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

export async function handleProposal(env, cq, fetchFn = fetch, now = Date.now()) {
  const p = parseProposal(cq.data);
  if (!p || !env.SUBS) {
    await call(env, "answerCallbackQuery", { callback_query_id: cq.id }, fetchFn);
    return null;
  }
  if (String(cq.from && cq.from.id) !== String(env.OWNER_CHAT_ID)) {
    await call(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "Solo il proprietario del bot può scegliere." },
      fetchFn);
    return null;
  }
  await saveDecision(env, p.id, p.d, now);
  await call(env, "answerCallbackQuery", {
    callback_query_id: cq.id,
    text: p.d === "s" ? "Approvata: si applica al prossimo giro (entro mezz'ora) e diventa un esempio del registro degli errori."
      : "Scartata: non cambia niente.",
  }, fetchFn);
  const msg = cq.message;
  const keyboard = msg ? markedKeyboard(msg.reply_markup, p.id, p.d) : null;
  if (keyboard) {
    await call(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id, reply_markup: keyboard },
      fetchFn);
  }
  return p;
}

// GET /proposte.json: le scelte sulle proposte (solo id e s/n), per il giro su GitHub
export async function decisionsList(env) {
  const decisions = env.SUBS ? await readDecisions(env) : {};
  return new Response(JSON.stringify({ decisioni: decisions }), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
      "access-control-allow-origin": "*" },
  });
}
