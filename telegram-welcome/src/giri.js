// HLX Watch - sorveglianza dei giri su GitHub.
//
// Ogni 10 minuti il bot legge comunque gli ultimi run del workflow per decidere se avviarne uno (src/dispatch.js):
// qui la stessa lista dice se qualcosa non va, senza altre richieste a GitHub. Tre guasti:
//   - "bloccato": un run in corso o in coda da più di STUCK_MS (con il limite di 300 minuti del lavoro e la coda dei
//     run, un passo fermo terrebbe il sito fermo per ore);
//   - "falliti": gli ultimi due run finiti sono falliti (uno solo può essere un intoppo di GitHub);
//   - "fermo": nessun run partito da più di GAP_MS, con l'avvio ogni 30 minuti attivo.
// Un solo avviso al proprietario per tipo di guasto, e uno quando tutto torna a posto. Il guasto attuale resta nel KV
// (giri:problema) e la pagina Stato del sito lo mostra.

export const STUCK_MS = 75 * 60 * 1000;    // un giro normale dura 2-6 minuti; l'archeologia a mano circa un'ora
export const GAP_MS = 80 * 60 * 1000;      // con un giro ogni 30 minuti, due giri saltati di fila
const KEY = "giri:problema";
const BUSY = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);
const IGNORED = new Set(["cancelled", "skipped"]);   // annullato a mano o saltato: non è un guasto

const minutes = (ms) => Math.floor(ms / 60000);

// Il guasto attuale, o null se è tutto a posto. `runs` come li dà l'API di GitHub, in qualunque ordine.
export function runProblem(runs, now = Date.now(), dispatching = true) {
  const sorted = [...(runs || [])].sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
  if (!sorted.length) return null;
  const busy = sorted.find((r) => BUSY.has(r.status));
  if (busy) {
    const age = now - (Date.parse(busy.created_at) || now);
    if (age > STUCK_MS) {
      return { tipo: "bloccato", minuti: minutes(age), stato: busy.status, url: busy.html_url || "", dal: busy.created_at };
    }
  }
  const done = sorted.filter((r) => r.status === "completed" && !IGNORED.has(r.conclusion));
  if (done.length >= 2 && done[0].conclusion === "failure" && done[1].conclusion === "failure") {
    return { tipo: "falliti", url: done[0].html_url || "", dal: done[1].created_at };
  }
  const age = now - (Date.parse(sorted[0].created_at) || now);
  if (dispatching && !busy && age > GAP_MS) {
    return { tipo: "fermo", minuti: minutes(age), url: sorted[0].html_url || "", dal: sorted[0].created_at };
  }
  return null;
}

const ora = (iso) => new Date(iso).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Rome" });

export function problemText(p) {
  const link = p.url ? `\nDettagli: ${p.url}` : "";
  if (p.tipo === "bloccato") {
    return `⚠️ <b>HLX Watch: un giro è ${p.stato === "queued" ? "in coda" : "in corso"} da ${p.minuti} minuti</b>\n` +
      "Un giro normale dura pochi minuti, e finché non finisce i successivi aspettano: il sito non si aggiorna. " +
      "Se non hai avviato tu l'archeologia o una scansione lunga, annullalo dalla scheda Actions: il giro dopo " +
      "riparte dall'ultimo archivio salvato." + link;
  }
  if (p.tipo === "falliti") {
    return "⚠️ <b>HLX Watch: gli ultimi due giri sono falliti</b>\n" +
      `Il sito resta fermo all'ultimo giro riuscito (falliscono dalle ${ora(p.dal)}). Nel registro del giro c'è il ` +
      "passo che si è fermato." + link;
  }
  return `⚠️ <b>HLX Watch: nessun giro da ${p.minuti} minuti</b>\n` +
    "Il bot dovrebbe avviarne uno ogni 30 minuti, ma GitHub non ne fa partire. Può essere un guasto di GitHub " +
    "(githubstatus.com) o il workflow disattivato nella scheda Actions." + link;
}

// Chiamata da dispatchRun con la lista dei run appena letta. Ritorna il guasto attuale (per i log e i test).
export async function watchRuns(env, runs, sendTelegram, now = Date.now(), dispatching = true) {
  if (!env.SUBS) return null;
  const p = runProblem(runs, now, dispatching);
  const saved = JSON.parse((await env.SUBS.get(KEY)) || "null");
  if (!p) {
    if (saved) {
      await env.SUBS.delete(KEY);
      if (env.OWNER_CHAT_ID) {
        await sendTelegram(env, env.OWNER_CHAT_ID, "✅ <b>HLX Watch: i giri su GitHub funzionano di nuovo</b>");
      }
    }
    return null;
  }
  // stesso tipo di guasto già segnalato: niente nuovo messaggio (il KV si riscrive solo se cambia il tipo, il
  // piano gratuito concede poche scritture al giorno)
  if (saved && saved.tipo === p.tipo) return p;
  await env.SUBS.put(KEY, JSON.stringify({ ...p, avvisato: new Date(now).toISOString() }));
  if (env.OWNER_CHAT_ID) await sendTelegram(env, env.OWNER_CHAT_ID, problemText(p));
  return p;
}

// Per /fast/stato.json: il guasto segnalato, se c'è
export async function runsStatus(env) {
  return env.SUBS ? JSON.parse((await env.SUBS.get(KEY)) || "null") : null;
}
