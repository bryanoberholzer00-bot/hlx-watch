// HLX Watch - risposta immediata ai comandi del bot Telegram.
//
// Il bot di HLX Watch è solo un mittente: gli avvisi partono dai run di GitHub Actions.
// Questo Cloudflare Worker riceve i messaggi in arrivo (webhook Telegram) e risponde
// subito a /start e /help, anche con il PC spento. Non legge né salva nulla.
//
// Variabili (wrangler secret put ...): BOT_TOKEN, WEBHOOK_SECRET, OWNER_CHAT_ID

const DASHBOARD = "https://bryanoberholzer00-bot.github.io/hlx-watch/";
const REPO = "https://github.com/bryanoberholzer00-bot/hlx-watch";

function welcome(isOwner) {
  if (isOwner) {
    return [
      "<b>Ciao, sei il proprietario di HLX Watch.</b>",
      "",
      "Qui ricevi:",
      "• gli avvisi immediati quando compare una stringa HLX mai vista;",
      "• il report delle ultime 24 ore, una volta al giorno in mattinata.",
      "",
      `Dashboard: ${DASHBOARD}`,
    ].join("\n");
  }
  return [
    "<b>Benvenuto in HLX Watch</b>",
    "",
    "Questo bot sorveglia CS2, Dota 2, Deadlock, Half-Life: Alyx e il client Steam alla ricerca di " +
      "riferimenti a <b>HLX</b>, il presunto nome in codice di Half-Life 3.",
    "",
    "Gli avvisi sono privati e arrivano solo al proprietario, ma tutte le scoperte sono pubbliche:",
    `• dashboard: ${DASHBOARD}`,
    `• codice: ${REPO}`,
    "",
    "Scrivi /help per rivedere questo messaggio.",
  ].join("\n");
}

async function reply(env, chatId, text) {
  const res = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!res.ok) console.log("sendMessage", res.status, await res.text());
}

export default {
  async fetch(request, env) {
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
    const text = (message && message.text) || "";
    const command = text.trim().split(/[\s@]/)[0].toLowerCase();
    if (message && message.chat && (command === "/start" || command === "/help")) {
      const isOwner = String(message.chat.id) === String(env.OWNER_CHAT_ID);
      await reply(env, message.chat.id, welcome(isOwner));
    }
    // Sempre 200: altrimenti Telegram ripete lo stesso aggiornamento all'infinito.
    return new Response("ok", { status: 200 });
  },
};
