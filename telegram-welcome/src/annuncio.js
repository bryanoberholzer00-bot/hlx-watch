// HLX Watch - modalità annuncio.
//
// Quando arriva un possibile annuncio (dal controllo veloce di src/fast.js o dal giro completo su GitHub, che lo manda
// con le notifiche del sito: messaggio con "annuncio": true su /push/send), HLX Watch passa in modalità annuncio per
// DURATA_MS dall'ultima voce:
//   - il giro completo su GitHub riparte ogni 10 minuti a tutte le ore (src/dispatch.js);
//   - /fast/stato.json la riporta, e il sito mette una fascia in cima a ogni pagina e la pagina annuncio.html;
//   - il proprietario riceve un messaggio, e con /annuncio_falso la spegne se era un falso allarme.
// Il KV si scrive solo quando la modalità parte, quando arriva una voce nuova o quando si spegne.

export const KEY = "annuncio:modo";
export const DURATA_MS = 14 * 24 * 3600 * 1000;
const MAX_VOCI = 8;

// La modalità attiva (o null), con i giorni passati dall'inizio
export async function modeStatus(env, now = Date.now()) {
  if (!env.SUBS) return null;
  let m;
  try { m = JSON.parse((await env.SUBS.get(KEY)) || "null"); } catch { return null; }
  if (!m || m.spenta || !(Date.parse(m.fino) > now)) return null;
  return { ...m, giorno: Math.floor((now - Date.parse(m.da)) / 86400000) + 1 };
}

// items: voci come in bot/ultime.json ({ x: testo, u: link, g: dove }) o { title, url }
export async function activate(env, items, sendTelegram, now = Date.now(), fonte = "controllo veloce") {
  if (!env.SUBS) return null;
  let old = null;
  try { old = JSON.parse((await env.SUBS.get(KEY)) || "null"); } catch { old = null; }
  const live = old && !old.spenta && Date.parse(old.fino) > now;
  const voci = (items || []).map((a) => ({
    x: String(a.x || a.title || "").replace(/^POSSIBILE ANNUNCIO:\s*/, "").slice(0, 160),
    u: String(a.u || a.url || ""), g: String(a.g || ""),
  })).filter((v) => v.x);
  const known = new Set((live ? old.voci : []).map((v) => v.x));
  const fresh = voci.filter((v) => !known.has(v.x));
  // la stessa voce di nuovo (il giro completo ritrova quello che il controllo veloce ha già visto): niente da scrivere
  if (live && !fresh.length) return { ...old, nuova: false };
  const iso = new Date(now).toISOString();
  const mode = live
    ? { ...old, ultimo: iso, fino: new Date(now + DURATA_MS).toISOString(), voci: [...old.voci, ...fresh].slice(-MAX_VOCI) }
    : { da: iso, ultimo: iso, fino: new Date(now + DURATA_MS).toISOString(), fonte, voci: fresh.slice(0, MAX_VOCI) };
  await env.SUBS.put(KEY, JSON.stringify(mode));
  if (!live && env.OWNER_CHAT_ID) {
    await sendTelegram(env, env.OWNER_CHAT_ID,
      "🔴 <b>HLX Watch: modalità annuncio attiva</b>\n" +
      "Per 14 giorni il giro completo riparte ogni 10 minuti a tutte le ore, il sito ha una fascia in cima e la " +
      "pagina «Dopo l'annuncio» conta i giorni e li confronta con Alyx.\n" +
      "Se è un falso allarme scrivimi /annuncio_falso e torna tutto come prima.");
  }
  return { ...mode, nuova: !live };
}

// /annuncio_falso: solo il proprietario. Resta scritto che è stata spenta, così il sito lo sa al giro dopo.
export async function switchOff(env, now = Date.now()) {
  const m = await modeStatus(env, now);
  if (!m) return false;
  const { giorno, ...saved } = m;
  await env.SUBS.put(KEY, JSON.stringify({ ...saved, spenta: new Date(now).toISOString() }));
  return true;
}

export function statusText(m, site) {
  if (!m) return "Nessuna modalità annuncio attiva: HLX Watch lavora come al solito (giro ogni 30 minuti, ogni 10 nelle ore calde).";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const lines = [`🔴 <b>Modalità annuncio: giorno ${m.giorno}</b>`,
    `Attiva fino al ${new Date(m.fino).toLocaleDateString("it-IT", { timeZone: "Europe/Rome" })}, giro ogni 10 minuti.`, ""];
  for (const v of m.voci) lines.push(`• ${esc(v.x)}${v.u ? `\n  ${esc(v.u)}` : ""}`);
  lines.push("", `${site}annuncio.html`, "Falso allarme? /annuncio_falso");
  return lines.join("\n");
}
