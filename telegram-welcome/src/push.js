// HLX Watch - notifiche di sistema dal sito (Web Push).
//
// Il sito chiede il permesso al browser e manda qui l'iscrizione (un indirizzo del servizio push di Google,
// Mozilla, Microsoft o Apple più due chiavi pubbliche). Le iscrizioni stanno nel KV di Cloudflare (binding
// SUBS), nei metadati delle chiavi: un solo list() le legge tutte. Quando i run su GitHub trovano novità
// chiamano POST /push/send con il segreto PUSH_SECRET; il Worker cifra il messaggio per ogni browser
// (RFC 8291, aes128gcm) e lo firma con la chiave VAPID (RFC 8292).
//
// Segreti: VAPID_PRIVATE_JWK (chiave privata P-256 in formato JWK), PUSH_SECRET.
// Variabile facoltativa solo per i test locali: PUSH_ALLOW_HTTP=1 accetta indirizzi http://127.0.0.1.

const SITE_ORIGIN = "https://bryanoberholzer00-bot.github.io";
const CONTACT = "https://github.com/bryanoberholzer00-bot/hlx-watch";
const MAX_SUBSCRIPTIONS = 2000;
const BATCH = 10;                 // browser per chiamata: il piano gratuito concede pochi ms di calcolo
const PUSH_HOSTS = [/\.googleapis\.com$/, /\.mozilla\.com$/, /\.mozaws\.net$/, /\.notify\.windows\.com$/,
  /\.push\.apple\.com$/];

const enc = new TextEncoder();

// ------------------------------------------------------------------ utilità
export function b64u(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64u(text) {
  const s = String(text).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
}

async function hkdf(salt, ikm, info, length) {
  const key = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info }, key, length * 8));
}

async function sha256hex(text) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(text)));
  return [...d].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ------------------------------------------------------------------ VAPID
export function vapidPublicKey(env) {
  const jwk = JSON.parse(env.VAPID_PRIVATE_JWK);
  return b64u(concat(new Uint8Array([4]), unb64u(jwk.x), unb64u(jwk.y)));
}

export async function vapidHeader(env, endpoint, now = Date.now()) {
  const jwk = JSON.parse(env.VAPID_PRIVATE_JWK);
  const key = await crypto.subtle.importKey("jwk", { kty: "EC", crv: "P-256", d: jwk.d, x: jwk.x, y: jwk.y },
    { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const head = b64u(enc.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const claims = b64u(enc.encode(JSON.stringify({
    aud: new URL(endpoint).origin, exp: Math.floor(now / 1000) + 12 * 3600, sub: CONTACT,
  })));
  // WebCrypto restituisce la firma già nel formato r||s richiesto dai JWT
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, enc.encode(`${head}.${claims}`));
  return `vapid t=${head}.${claims}.${b64u(sig)}, k=${vapidPublicKey(env)}`;
}

// ------------------------------------------------------------------ cifratura (RFC 8291)
export async function encryptPayload(subscription, text) {
  const uaPublic = unb64u(subscription.keys.p256dh);
  const authSecret = unb64u(subscription.keys.auth);
  const local = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const asPublic = new Uint8Array(await crypto.subtle.exportKey("raw", local.publicKey));
  const uaKey = await crypto.subtle.importKey("raw", uaPublic, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, local.privateKey, 256));

  const ikm = await hkdf(authSecret, shared, concat(enc.encode("WebPush: info\0"), uaPublic, asPublic), 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, enc.encode("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await hkdf(salt, ikm, enc.encode("Content-Encoding: nonce\0"), 12);

  const plain = concat(enc.encode(text), new Uint8Array([2]));        // 0x02: ultimo (e unico) record
  const aes = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, aes, plain));

  const header = new Uint8Array(16 + 4 + 1 + asPublic.length);
  header.set(salt, 0);
  new DataView(header.buffer).setUint32(16, 4096);
  header[20] = asPublic.length;
  header.set(asPublic, 21);
  return concat(header, cipher);
}

async function sendOne(env, subscription, payload, vapidCache) {
  const origin = new URL(subscription.endpoint).origin;
  if (!vapidCache[origin]) vapidCache[origin] = await vapidHeader(env, subscription.endpoint);
  const body = await encryptPayload(subscription, payload);
  const res = await fetch(subscription.endpoint, {
    method: "POST",
    headers: {
      Authorization: vapidCache[origin], TTL: "86400", Urgency: "normal",
      "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
    },
    body,
  });
  if (res.status >= 400) {
    // il servizio push spiega il rifiuto nel testo della risposta: utile per capire i problemi
    console.log("rifiuto del servizio push", origin, res.status, (await res.text()).slice(0, 300));
  }
  return res.status;
}

// ------------------------------------------------------------------ iscrizioni
function validSubscription(sub, env) {
  if (!sub || typeof sub.endpoint !== "string" || sub.endpoint.length > 1000) return false;
  if (!sub.keys || typeof sub.keys.p256dh !== "string" || typeof sub.keys.auth !== "string") return false;
  let url;
  try { url = new URL(sub.endpoint); } catch { return false; }
  if (env.PUSH_ALLOW_HTTP === "1" && url.protocol === "http:" && url.hostname === "127.0.0.1") return true;
  return url.protocol === "https:" && PUSH_HOSTS.some((re) => re.test(url.hostname));
}

const subKey = async (endpoint) => `sub:${await sha256hex(endpoint)}`;

// KV restituisce al massimo 1000 chiavi per chiamata: si conta a pagine
async function countSubscriptions(env, stopAt = Infinity) {
  let count = 0;
  let cursor;
  do {
    const page = await env.SUBS.list({ prefix: "sub:", limit: 1000, cursor });
    count += page.keys.length;
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor && count < stopAt);
  return count;
}

function cors(request, extra = {}) {
  const origin = request.headers.get("Origin") || "";
  return {
    "Access-Control-Allow-Origin": origin === SITE_ORIGIN || origin.startsWith("http://localhost") ? origin : SITE_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
    ...extra,
  };
}

const json = (request, data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: cors(request, { "Content-Type": "application/json" }) });

function authorized(request, env) {
  const got = request.headers.get("Authorization") || "";
  const want = `Bearer ${env.PUSH_SECRET || ""}`;
  if (!env.PUSH_SECRET || got.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < got.length; i++) diff |= got.charCodeAt(i) ^ want.charCodeAt(i);
  return diff === 0;
}

const welcome = (request) => JSON.stringify({
  ack: `${new URL(request.url).origin}/push/ack`,
  title: "HLX Watch: notifiche attive",
  body: "Riceverai un avviso quando compare una novità rilevante (HLX, Half-Life 3, nuove build...).",
  url: "./", tag: "hlx-benvenuto",
});

// ------------------------------------------------------------------ invio e orario di silenzio
async function sendPage(env, payload, cursor, limit = BATCH) {
  const page = await env.SUBS.list({ prefix: "sub:", limit, cursor });
  const vapidCache = {};
  const statuses = {};
  let removed = 0;
  for (const k of page.keys) {
    if (!k.metadata) continue;
    let status;
    try { status = await sendOne(env, k.metadata, payload, vapidCache); } catch (err) { status = String(err).slice(0, 60); }
    statuses[status] = (statuses[status] || 0) + 1;
    if (status === 404 || status === 410) {          // il browser ha revocato l'iscrizione
      await env.SUBS.delete(k.name);
      removed += 1;
    }
  }
  return { sent: page.keys.length, statuses, removed, next: page.list_complete ? null : page.cursor };
}

// Ora italiana (con l'ora legale gestita da Intl): silenzio da QUIET_START a QUIET_END, di default 23-8.
export function localHour(env, now = new Date()) {
  return Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: env.QUIET_TZ || "Europe/Rome", hour: "2-digit", hourCycle: "h23",
  }).format(now));
}

export function isQuiet(env, now = new Date()) {
  const start = Number(env.QUIET_START ?? 23);
  const end = Number(env.QUIET_END ?? 8);
  if (start === end) return false;
  const h = localHour(env, now);
  return start > end ? (h >= start || h < end) : (h >= start && h < end);
}

const PENDING = "pending:notte";

async function queueForMorning(env, message) {
  const list = JSON.parse((await env.SUBS.get(PENDING)) || "[]");
  list.push({ title: String(message.title || ""), body: String(message.body || ""), url: String(message.url || "") });
  await env.SUBS.put(PENDING, JSON.stringify(list.slice(-50)));
}

export function morningSummary(list, site) {
  if (list.length === 1) return { ...list[0], tag: `hlx-mattino-${Date.now()}` };
  const lines = list.slice(0, 4).map((m) => `• ${m.title.replace(/^HLX Watch:\s*/, "")}`);
  if (list.length > 4) lines.push(`… e altri ${list.length - 4} avvisi`);
  return {
    title: `HLX Watch: novità della notte (${list.length} avvisi)`,
    body: lines.join("\n"),
    url: `${site}search.html?new=1&min=50`,
    tag: `hlx-mattino-${Date.now()}`,
  };
}

// Chiamata ogni ora dal cron del Worker: finito il silenzio, manda il riassunto e svuota la coda.
export async function flushMorning(env, site, ackOrigin) {
  if (!env.SUBS || !env.VAPID_PRIVATE_JWK || isQuiet(env)) return null;
  const raw = await env.SUBS.get(PENDING);
  if (!raw) return null;
  const list = JSON.parse(raw);
  if (!list.length) {
    await env.SUBS.delete(PENDING);
    return null;
  }
  const payload = JSON.stringify({ ...morningSummary(list, site), ack: `${ackOrigin}/push/ack` });
  const total = { sent: 0, removed: 0 };
  let cursor;
  try {
    do {
      const res = await sendPage(env, payload, cursor, 50);
      total.sent += res.sent;
      total.removed += res.removed;
      cursor = res.next || undefined;
    } while (cursor);
  } catch (err) {
    // la coda resta nel KV: si ritenta all'ora successiva invece di perdere gli avvisi della notte
    console.log("riassunto della notte non inviato, ritento tra un'ora", String(err));
    return null;
  }
  await env.SUBS.delete(PENDING);      // svuotata solo dopo l'invio riuscito
  console.log("riassunto della notte inviato", list.length, "avvisi a", total.sent, "browser");
  return total;
}

export async function handlePush(request, env) {
  const path = new URL(request.url).pathname;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(request) });
  if (!env.SUBS || !env.VAPID_PRIVATE_JWK) return json(request, { error: "notifiche non configurate" }, 503);

  if (path === "/push/key" && request.method === "GET") {
    return json(request, { key: vapidPublicKey(env) });
  }

  if (path === "/push/subscribe" && request.method === "POST") {
    let sub;
    try { sub = await request.json(); } catch { return json(request, { error: "json non valido" }, 400); }
    if (!validSubscription(sub, env)) return json(request, { error: "iscrizione non valida" }, 400);
    const key = await subKey(sub.endpoint);
    const record = { endpoint: sub.endpoint, keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth } };
    const known = await env.SUBS.getWithMetadata(key);
    // il sito ripete l'iscrizione a ogni visita: se c'è già non si scrive nulla e non si manda il benvenuto
    if (known.metadata && known.metadata.keys?.p256dh === record.keys.p256dh) return json(request, { ok: true, already: true });
    if (!known.metadata) {
      if (await countSubscriptions(env, MAX_SUBSCRIPTIONS) >= MAX_SUBSCRIPTIONS) return json(request, { error: "troppe iscrizioni" }, 429);
    }
    await env.SUBS.put(key, "1", { metadata: record });
    let status = null;
    try { status = await sendOne(env, record, welcome(request), {}); } catch (err) { console.log("benvenuto", String(err)); }
    if (status === 404 || status === 410 || status === 403) {
      // il servizio push rifiuta subito l'indirizzo (o la chiave): inutile tenerlo
      await env.SUBS.delete(key);
      return json(request, { error: `il servizio push ha rifiutato l'iscrizione (${status})` }, 502);
    }
    return json(request, { ok: true, welcome: status });
  }

  if (path === "/push/unsubscribe" && request.method === "POST") {
    let sub;
    try { sub = await request.json(); } catch { return json(request, { error: "json non valido" }, 400); }
    if (typeof sub?.endpoint !== "string") return json(request, { error: "manca endpoint" }, 400);
    await env.SUBS.delete(await subKey(sub.endpoint));
    return json(request, { ok: true });
  }

  if (path === "/push/send" && request.method === "POST") {
    if (!authorized(request, env)) return json(request, { error: "non autorizzato" }, 403);
    let body;
    try { body = await request.json(); } catch { return json(request, { error: "json non valido" }, 400); }
    const message = body.message || {};
    // di notte le novità si mettono da parte: alle 8 parte un solo riassunto (le prove passano sempre)
    if (!message.urgente && isQuiet(env)) {
      if (!body.cursor) await queueForMorning(env, message);
      return json(request, { queued: true, sent: 0, statuses: {}, removed: 0, next: null });
    }
    // il service worker del sito risponde su /push/ack: così si sa se la notifica è stata davvero mostrata
    const payload = JSON.stringify({ ...message, ack: `${new URL(request.url).origin}/push/ack` });
    if (payload.length > 3000) return json(request, { error: "messaggio troppo lungo" }, 400);
    return json(request, await sendPage(env, payload, body.cursor || undefined));
  }

  if (path === "/push/ack" && request.method === "POST") {
    let info = {};
    try { info = JSON.parse(await request.text()); } catch { /* ignorato */ }
    console.log("conferma di consegna", String(info.tag || "").slice(0, 40), String(info.esito || "").slice(0, 200));
    return new Response(null, { status: 204, headers: cors(request) });
  }

  if (path === "/push/count" && request.method === "GET") {
    if (!authorized(request, env)) return json(request, { error: "non autorizzato" }, 403);
    return json(request, { iscritti: await countSubscriptions(env) });
  }

  return json(request, { error: "non trovato" }, 404);
}
