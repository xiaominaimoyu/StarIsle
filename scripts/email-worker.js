/**
 * 星屿 StarIsle · Cloudflare Email Worker 参考实现。
 *
 * 职责：接收原始 MIME → 计算 kid 绑定 HMAC → 转发 FastAPI。
 * 说明：worker 不向浏览器转发原始内容；API 端再校验时间窗 / nonce / Message-ID 幂等。
 */

const PERMANENT_API_STATUSES = new Set([413, 422]);

/**
 * @typedef {{
 *   MAIL_HMAC_KEY: string;
 *   MAIL_HMAC_ACTIVE_KID?: string;
 *   API_BASE_URL: string;
 *   MAIL_MAX_BYTES: string | number;
 * }} MailWorkerEnv
 */

/**
 * @typedef {{
 *   cryptoImpl?: Crypto;
 *   fetchImpl?: typeof fetch;
 *   now?: () => number;
 *   randomUUID?: () => string;
 *   log?: (message: string) => void;
 * }} MailWorkerDependencies
 */

class MailForwardError extends Error {
  /**
   * @param {string} code
   * @param {boolean} [permanent]
   */
  constructor(code, permanent = false) {
    super(code);
    this.code = code;
    this.permanent = permanent;
  }
}

/**
 * 只接受完整 origin，避免把 Worker 的 HMAC 正文投递到意外路径或携带查询参数的 URL。
 *
 * @param {unknown} value
 * @returns {string}
 */
export function validateApiBaseUrl(value) {
  if (typeof value !== "string" || !value || value.trim() !== value) {
    throw new MailForwardError("invalid_api_base_url");
  }
  let base;
  try {
    base = new URL(value);
  } catch {
    throw new MailForwardError("invalid_api_base_url");
  }
  if (
    !["http:", "https:"].includes(base.protocol)
    || base.pathname !== "/"
    || base.search
    || base.hash
    || base.username
    || base.password
  ) {
    throw new MailForwardError("invalid_api_base_url");
  }
  // 生产投递只接受 HTTPS。HTTP 仅保留给本机 loopback smoke，避免把已签名
  // 的原始 MIME 意外投递到远程明文端点。
  const hostname = base.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const isLoopback = hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
  if (base.protocol === "http:" && !isLoopback) {
    throw new MailForwardError("invalid_api_base_url");
  }
  return new URL("/api/v1/mail/receive", base.origin).toString();
}

/**
 * @param {unknown} value
 * @returns {number}
 */
export function parseMaxBytes(value) {
  const text = typeof value === "number" ? String(value) : value;
  if (typeof text !== "string" || !/^[1-9][0-9]*$/.test(text)) {
    throw new MailForwardError("invalid_mail_max_bytes");
  }
  const parsed = Number(text);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new MailForwardError("invalid_mail_max_bytes");
  }
  return parsed;
}

/**
 * Missing or exactly empty keeps the staged legacy protocol; configured values
 * must use the same bounded ASCII kid grammar as the API.
 *
 * @param {unknown} value
 * @returns {string | null}
 */
export function parseOptionalActiveKid(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)) {
    throw new MailForwardError("invalid_mail_hmac_active_kid");
  }
  return value;
}

/**
 * @param {MailWorkerEnv} env
 * @returns {{ endpoint: string; hmacKey: string; activeKid: string | null; maxBytes: number }}
 */
export function validateEnv(env) {
  const endpoint = validateApiBaseUrl(env?.API_BASE_URL);
  if (typeof env?.MAIL_HMAC_KEY !== "string" || !env.MAIL_HMAC_KEY.trim()) {
    throw new MailForwardError("invalid_hmac_key");
  }
  return {
    endpoint,
    hmacKey: env.MAIL_HMAC_KEY,
    activeKid: parseOptionalActiveKid(env.MAIL_HMAC_ACTIVE_KID),
    maxBytes: parseMaxBytes(env.MAIL_MAX_BYTES),
  };
}

/**
 * @param {ArrayBuffer | ArrayBufferView | Uint8Array} value
 * @returns {Uint8Array}
 */
function asBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new MailForwardError("invalid_raw_chunk");
}

/**
 * 在发现实际流超限时立即停下；不会使用 Response(...).arrayBuffer() 无界读取。
 *
 * @param {ReadableStream<Uint8Array>} raw
 * @param {number} maxBytes
 * @returns {Promise<Uint8Array>}
 */
export async function readBoundedRaw(raw, maxBytes) {
  if (!raw || typeof raw.getReader !== "function") {
    throw new MailForwardError("invalid_raw_stream");
  }
  const reader = raw.getReader();
  /** @type {Uint8Array[]} */
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = asBytes(value);
      if (chunk.byteLength > maxBytes - total) {
        throw new MailForwardError("raw_body_too_large", true);
      }
      chunks.push(chunk);
      total += chunk.byteLength;
    }
  } finally {
    reader.releaseLock();
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

/**
 * @param {BufferSource} data
 * @param {Crypto} cryptoImpl
 * @returns {Promise<string>}
 */
export async function sha256Hex(data, cryptoImpl) {
  if (!cryptoImpl?.subtle) throw new MailForwardError("crypto_unavailable");
  const digest = await cryptoImpl.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * @param {string} hmacKey
 * @param {string} payload
 * @param {Crypto} cryptoImpl
 * @returns {Promise<string>}
 */
export async function hmacSha256Hex(hmacKey, payload, cryptoImpl) {
  if (!cryptoImpl?.subtle) throw new MailForwardError("crypto_unavailable");
  const encoder = new TextEncoder();
  const key = await cryptoImpl.subtle.importKey(
    "raw",
    encoder.encode(hmacKey),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await cryptoImpl.subtle.sign("HMAC", key, encoder.encode(payload));
  return [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * @returns {Required<MailWorkerDependencies>}
 */
function runtimeDependencies() {
  return {
    cryptoImpl: globalThis.crypto,
    fetchImpl: typeof globalThis.fetch === "function" ? globalThis.fetch.bind(globalThis) : undefined,
    now: () => Date.now(),
    randomUUID: () => globalThis.crypto?.randomUUID?.() || "",
    log: (message) => console.error(message),
  };
}

/**
 * 转发一封邮件。依赖可注入，以便本地单元测试无需全局 monkeypatch。
 *
 * 只有邮件本身永久不可接受（本地大小限制或 API 413/422）才调用 setReject。
 * 认证、限流、服务端、网络及配置故障均记录无敏感通用错误并抛出通用异常。
 *
 * @param {import("@cloudflare/workers-types").EmailMessage} message
 * @param {MailWorkerEnv} env
 * @param {MailWorkerDependencies} [dependencies]
 * @returns {Promise<{ status: number | null; id: string | null; rejected: boolean }>}
 */
export async function forwardEmail(message, env, dependencies = {}) {
  const runtime = { ...runtimeDependencies(), ...dependencies };
  let rejectReason = "Message cannot be accepted";
  try {
    const { endpoint, hmacKey, activeKid, maxBytes } = validateEnv(env);
    if (!Number.isSafeInteger(message?.rawSize) || message.rawSize < 0) {
      throw new MailForwardError("invalid_raw_size");
    }
    if (message.rawSize > maxBytes) {
      rejectReason = "Message exceeds configured size limit";
      throw new MailForwardError("declared_body_too_large", true);
    }

    const bodyBytes = await readBoundedRaw(message.raw, maxBytes);
    if (bodyBytes.byteLength !== message.rawSize) {
      throw new MailForwardError("raw_size_mismatch");
    }
    const timestamp = String(Math.floor(runtime.now() / 1000));
    const nonce = runtime.randomUUID();
    if (typeof nonce !== "string" || !nonce) {
      throw new MailForwardError("nonce_unavailable");
    }
    const bodySha = await sha256Hex(bodyBytes, runtime.cryptoImpl);
    const signature = await hmacSha256Hex(
      hmacKey,
      activeKid === null
        ? `${timestamp}.${nonce}.${bodySha}`
        : `v1.${activeKid}.${timestamp}.${nonce}.${bodySha}`,
      runtime.cryptoImpl,
    );
    const headers = {
      "Content-Type": "message/rfc822",
      "x-mail-timestamp": timestamp,
      "x-mail-nonce": nonce,
      "x-mail-signature": signature,
    };
    if (activeKid !== null) headers["x-mail-key-id"] = activeKid;

    let response;
    try {
      response = await runtime.fetchImpl(endpoint, {
        method: "POST",
        headers,
        body: bodyBytes,
      });
    } catch {
      throw new MailForwardError("transport_failure");
    }
    if (!response || !Number.isInteger(response.status)) {
      throw new MailForwardError("invalid_api_response");
    }
    if (response.ok) {
      let id = null;
      try {
        const receipt = typeof response.json === "function" ? await response.json() : null;
        if (receipt && typeof receipt.id === "string") id = receipt.id;
      } catch {
        // 成功状态不依赖下游响应体；绝不把响应体写入日志。
      }
      return { status: response.status, id, rejected: false };
    }
    if (PERMANENT_API_STATUSES.has(response.status)) {
      throw new MailForwardError("permanent_api_rejection", true);
    }
    throw new MailForwardError("temporary_api_failure");
  } catch (error) {
    if (error instanceof MailForwardError && error.permanent) {
      try {
        message.setReject(rejectReason);
        return { status: null, id: null, rejected: true };
      } catch {
        runtime.log("mail forward failed");
        throw new Error("mail forwarding failed");
      }
    }
    runtime.log("mail forward failed");
    throw new Error("mail forwarding failed");
  }
}

export default {
  /**
   * @param {import("@cloudflare/workers-types").EmailMessage} message
   * @param {MailWorkerEnv} env
   * @param {import("@cloudflare/workers-types").ExecutionContext} _ctx
   */
  async email(message, env, _ctx) {
    return forwardEmail(message, env);
  },
};
