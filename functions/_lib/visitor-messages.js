/** Public visitor notes and the desk's fetch/acknowledge endpoint. */

const NAME_MAX = 80;
const CONTACT_MAX = 120;
const BODY_MAX = 2000;
const REQUEST_MAX_BYTES = 8192;
const RATE_LIMIT = 5;
const RATE_WINDOW_MS = 10 * 60 * 1000;
const PULL_LIMIT = 20;
const ACK_LIMIT = 50;
const MESSAGE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
};

export function jsonResponse(status, code, extra = {}) {
  return new Response(JSON.stringify({ error: { code, ...extra } }), {
    status,
    headers: JSON_HEADERS,
  });
}

export function dataResponse(status, payload) {
  return new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS });
}

function cleanText(value, maxLength, { allowNewlines = false, minLength = 1 } = {}) {
  if (typeof value !== "string") return null;
  const withoutControls = Array.from(value.normalize("NFKC"))
    .filter((char) => {
      const code = char.charCodeAt(0);
      if (code === 10 && allowNewlines) return true;
      return code > 31 && code !== 127;
    })
    .join("");
  const text = allowNewlines ? withoutControls.trim() : withoutControls.replace(/\s+/g, " ").trim();
  if (text.length < minLength || text.length > maxLength) return null;
  return text;
}

export function parseVisitorMessage(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const name = cleanText(input.name, NAME_MAX);
  const contact = cleanText(input.contact, CONTACT_MAX, { minLength: 3 });
  const body = cleanText(input.body, BODY_MAX, { allowNewlines: true });
  const turnstileToken = typeof input.turnstileToken === "string" ? input.turnstileToken.trim() : "";
  if (!name || !contact || !body || turnstileToken.length < 10 || turnstileToken.length > 4096) return null;
  return { name, contact, body, turnstileToken };
}

export function timingSafeEqual(left, right) {
  const encoder = new TextEncoder();
  const a = encoder.encode(String(left));
  const b = encoder.encode(String(right));
  const length = Math.max(a.length, b.length, 1);
  let diff = a.length ^ b.length;
  for (let index = 0; index < length; index += 1) {
    diff |= (a[index] || 0) ^ (b[index] || 0);
  }
  return diff === 0 && a.length > 0;
}

export function authorizedPull(header, expectedKey) {
  if (typeof expectedKey !== "string" || expectedKey.length < 20) return false;
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
  return timingSafeEqual(header.slice("Bearer ".length), expectedKey);
}

export function parseAcknowledgeIds(input) {
  const raw = input && Array.isArray(input.acknowledge) ? input.acknowledge : [];
  if (raw.length > ACK_LIMIT) return null;
  const ids = [];
  for (const value of raw) {
    if (typeof value !== "string" || !MESSAGE_ID.test(value) || ids.includes(value)) return null;
    ids.push(value);
  }
  return ids;
}

export async function hashIp(ip) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(ip || "unknown"));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

export async function verifyTurnstile({ fetchImpl, secret, token, ip }) {
  if (!secret || !token) return false;
  const body = new URLSearchParams();
  body.set("secret", secret);
  body.set("response", token);
  if (ip) body.set("remoteip", ip);
  const response = await fetchImpl("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) return false;
  const payload = await response.json();
  return payload.success === true;
}

export function createD1Store(database) {
  return {
    async rateHits(bucket) {
      const row = await database.prepare("SELECT hits FROM visitor_rate WHERE bucket = ?").bind(bucket).first();
      return row ? Number(row.hits) : 0;
    },
    async recordHit(bucket, windowStart) {
      await database
        .prepare(
          "INSERT INTO visitor_rate (bucket, hits, window_start) VALUES (?, 1, ?) "
            + "ON CONFLICT(bucket) DO UPDATE SET hits = hits + 1",
        )
        .bind(bucket, windowStart)
        .run();
    },
    async insertMessage(message) {
      await database
        .prepare("INSERT INTO visitor_message (id, created_at, name, contact, body) VALUES (?, ?, ?, ?, ?)")
        .bind(message.id, message.createdAt, message.name, message.contact, message.body)
        .run();
    },
    async deleteByIds(ids) {
      if (ids.length === 0) return 0;
      const marks = ids.map(() => "?").join(", ");
      const result = await database
        .prepare(`DELETE FROM visitor_message WHERE id IN (${marks})`)
        .bind(...ids)
        .run();
      return Number(result.meta?.changes || 0);
    },
    async takeOldest(limit) {
      const result = await database
        .prepare("SELECT id, created_at, name, contact, body FROM visitor_message ORDER BY created_at, id LIMIT ?")
        .bind(limit)
        .all();
      return (result.results || []).map((row) => ({
        id: row.id,
        createdAt: row.created_at,
        name: row.name,
        contact: row.contact,
        body: row.body,
      }));
    },
  };
}

export function createMemoryStore() {
  const messages = [];
  const rates = new Map();
  return {
    async rateHits(bucket) {
      return rates.get(bucket) || 0;
    },
    async recordHit(bucket) {
      rates.set(bucket, (rates.get(bucket) || 0) + 1);
    },
    async insertMessage(message) {
      messages.push(message);
    },
    async deleteByIds(ids) {
      const drop = new Set(ids);
      const before = messages.length;
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        if (drop.has(messages[index].id)) messages.splice(index, 1);
      }
      return before - messages.length;
    },
    async takeOldest(limit) {
      return messages.slice(0, limit);
    },
  };
}

async function readLimitedJson(request) {
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > REQUEST_MAX_BYTES) return { error: "PAYLOAD_TOO_LARGE" };
  const text = await request.text();
  if (text.length > REQUEST_MAX_BYTES) return { error: "PAYLOAD_TOO_LARGE" };
  try {
    return { value: JSON.parse(text || "{}") };
  } catch {
    return { error: "BAD_REQUEST" };
  }
}

export async function submitVisitorMessage({ request, store, env, fetchImpl, now, randomUUID, ip }) {
  if (!store) return jsonResponse(503, "STORE_UNAVAILABLE");
  const parsed = await readLimitedJson(request);
  if (parsed.error) return jsonResponse(parsed.error === "PAYLOAD_TOO_LARGE" ? 413 : 400, parsed.error);
  const message = parseVisitorMessage(parsed.value);
  if (!message) return jsonResponse(400, "BAD_REQUEST");
  const accepted = await verifyTurnstile({
    fetchImpl,
    secret: env.TURNSTILE_SECRET_KEY,
    token: message.turnstileToken,
    ip,
  });
  if (!accepted) return jsonResponse(403, "TURNSTILE_FAILED");
  const windowStart = Math.floor(now() / RATE_WINDOW_MS);
  const bucket = `${await hashIp(ip)}:${windowStart}`;
  if ((await store.rateHits(bucket)) >= RATE_LIMIT) return jsonResponse(429, "RATE_LIMITED");
  await store.recordHit(bucket, windowStart);
  await store.insertMessage({
    id: randomUUID(),
    createdAt: new Date(now()).toISOString(),
    name: message.name,
    contact: message.contact,
    body: message.body,
  });
  return dataResponse(201, { ok: true });
}

export async function pullVisitorMessages({ request, store, env }) {
  if (!store) return jsonResponse(503, "STORE_UNAVAILABLE");
  if (!authorizedPull(request.headers.get("authorization"), env.VISITOR_PULL_KEY)) {
    return jsonResponse(401, "PULL_UNAUTHORIZED");
  }
  const parsed = await readLimitedJson(request);
  if (parsed.error) return jsonResponse(parsed.error === "PAYLOAD_TOO_LARGE" ? 413 : 400, parsed.error);
  const ids = parseAcknowledgeIds(parsed.value);
  if (ids === null) return jsonResponse(400, "BAD_REQUEST");
  const removed = await store.deleteByIds(ids);
  const messages = await store.takeOldest(PULL_LIMIT);
  return dataResponse(200, { removed, messages });
}

export async function handleVisitorSubmit(request, env, deps = {}) {
  const store = env.VISITOR_DB ? createD1Store(env.VISITOR_DB) : null;
  return submitVisitorMessage({
    request,
    store,
    env,
    fetchImpl: deps.fetchImpl || fetch,
    now: deps.now || Date.now,
    randomUUID: deps.randomUUID || (() => crypto.randomUUID()),
    ip: request.headers.get("cf-connecting-ip") || "",
  });
}

export async function handleVisitorPull(request, env) {
  const store = env.VISITOR_DB ? createD1Store(env.VISITOR_DB) : null;
  return pullVisitorMessages({ request, store, env });
}
