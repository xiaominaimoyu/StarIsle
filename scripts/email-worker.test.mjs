import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash, createHmac, webcrypto } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { forwardEmail, validateApiBaseUrl } from "./email-worker.js";
import { privateReceiptPath, runSmoke, writePrivateReceipt } from "./email-worker-smoke.mjs";

const encoder = new TextEncoder();
const DEFAULT_ENV = {
  API_BASE_URL: "http://127.0.0.1:8000",
  MAIL_HMAC_KEY: "worker-test-hmac-key",
  MAIL_HMAC_ACTIVE_KID: "v1",
  MAIL_MAX_BYTES: "4096",
};

function streamFromChunks(chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

function messageFromBytes(bytes, options = {}) {
  const rejected = [];
  return {
    rawSize: options.rawSize ?? bytes.byteLength,
    raw: options.raw ?? streamFromChunks(options.chunks ?? [bytes]),
    setReject(reason) {
      rejected.push(reason);
    },
    rejected,
  };
}

function dependencies(fetchImpl, logs, now = 1_700_000_000_000) {
  return {
    cryptoImpl: webcrypto,
    fetchImpl,
    now: () => now,
    randomUUID: () => "nonce-12345678",
    log: (message) => logs.push(message),
  };
}

function response(status, body = { id: "mail-worker-test" }) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  };
}

test("forwards the exact MIME bytes with kid-bound canonical lowercase SHA-256 HMAC headers", async () => {
  const body = encoder.encode("From: sender@example.test\r\n\r\nworker body");
  const mail = messageFromBytes(body);
  const logs = [];
  let request;
  const result = await forwardEmail(
    mail,
    DEFAULT_ENV,
    dependencies(async (url, init) => {
      request = { url, init };
      return response(200);
    }, logs),
  );

  assert.deepEqual(result, { status: 200, id: "mail-worker-test", rejected: false });
  assert.equal(mail.rejected.length, 0);
  assert.deepEqual(logs, []);
  assert.equal(request.url, "http://127.0.0.1:8000/api/v1/mail/receive");
  assert.equal(request.init.method, "POST");
  assert.equal(request.init.headers["Content-Type"], "message/rfc822");
  assert.equal(request.init.headers["x-mail-timestamp"], "1700000000");
  assert.equal(request.init.headers["x-mail-nonce"], "nonce-12345678");
  assert.equal(request.init.headers["x-mail-key-id"], "v1");
  assert.deepEqual([...request.init.body], [...body]);

  const bodySha = createHash("sha256").update(body).digest("hex");
  const expectedSignature = createHmac("sha256", DEFAULT_ENV.MAIL_HMAC_KEY)
    .update("v1.v1.1700000000.nonce-12345678." + bodySha)
    .digest("hex");
  assert.equal(request.init.headers["x-mail-signature"], expectedSignature);
});

test("missing MAIL_HMAC_ACTIVE_KID keeps staged legacy headers and payload", async () => {
  const body = encoder.encode("From: sender@example.test\r\n\r\nlegacy worker body");
  let request;
  await forwardEmail(
    messageFromBytes(body),
    { ...DEFAULT_ENV, MAIL_HMAC_ACTIVE_KID: "" },
    dependencies(async (url, init) => {
      request = { url, init };
      return response(200);
    }, []),
  );

  assert.equal(Object.hasOwn(request.init.headers, "x-mail-key-id"), false);
  const bodySha = createHash("sha256").update(body).digest("hex");
  assert.equal(
    request.init.headers["x-mail-signature"],
    createHmac("sha256", DEFAULT_ENV.MAIL_HMAC_KEY)
      .update("1700000000.nonce-12345678." + bodySha)
      .digest("hex"),
  );
});

test("declared oversize rejects before touching the raw stream", async () => {
  const logs = [];
  let rawRead = false;
  let fetchCalled = false;
  const mail = {
    rawSize: 11,
    get raw() {
      rawRead = true;
      throw new Error("raw must not be read");
    },
    rejected: [],
    setReject(reason) {
      this.rejected.push(reason);
    },
  };
  const result = await forwardEmail(
    mail,
    { ...DEFAULT_ENV, MAIL_MAX_BYTES: "10" },
    dependencies(async () => {
      fetchCalled = true;
      return response(200);
    }, logs),
  );

  assert.equal(result.rejected, true);
  assert.equal(rawRead, false);
  assert.equal(fetchCalled, false);
  assert.equal(mail.rejected.length, 1);
  assert.deepEqual(logs, []);
});

test("accepts an exact declared and observed size boundary", async () => {
  const body = encoder.encode("12345");
  const mail = messageFromBytes(body);
  const result = await forwardEmail(
    mail,
    { ...DEFAULT_ENV, MAIL_MAX_BYTES: String(body.byteLength) },
    dependencies(async () => response(201, { id: "mail-boundary" }), []),
  );
  assert.deepEqual(result, { status: 201, id: "mail-boundary", rejected: false });
  assert.equal(mail.rejected.length, 0);
});

test("actual stream overflow is permanently rejected after bounded read detects it", async () => {
  const logs = [];
  const body = encoder.encode("123456");
  const mail = messageFromBytes(body, {
    rawSize: 5,
    chunks: [body],
  });
  const result = await forwardEmail(
    mail,
    { ...DEFAULT_ENV, MAIL_MAX_BYTES: "5" },
    dependencies(async () => response(200), logs),
  );
  assert.equal(result.rejected, true);
  assert.equal(mail.rejected.length, 1);
  assert.deepEqual(logs, []);
});

test("invalid configuration, URL, and rawSize throw without permanently rejecting", async () => {
  const cases = [
    { ...DEFAULT_ENV, API_BASE_URL: "https://api.example.test/not-an-origin" },
    { ...DEFAULT_ENV, API_BASE_URL: "ftp://api.example.test" },
    { ...DEFAULT_ENV, MAIL_HMAC_KEY: "" },
    { ...DEFAULT_ENV, MAIL_HMAC_ACTIVE_KID: "not safe" },
    { ...DEFAULT_ENV, MAIL_HMAC_ACTIVE_KID: "v".repeat(65) },
    { ...DEFAULT_ENV, MAIL_MAX_BYTES: "0" },
    { ...DEFAULT_ENV, MAIL_MAX_BYTES: "1.5" },
  ];

  for (const env of cases) {
    const body = encoder.encode("body");
    const mail = messageFromBytes(body);
    const logs = [];
    await assert.rejects(
      () => forwardEmail(mail, env, dependencies(async () => response(200), logs)),
      /mail forwarding failed/,
    );
    assert.equal(mail.rejected.length, 0);
    assert.deepEqual(logs, ["mail forward failed"]);
    if (env.MAIL_HMAC_KEY) assert.equal(logs.join("\n").includes(env.MAIL_HMAC_KEY), false);
  }

  const body = encoder.encode("body");
  const mail = messageFromBytes(body, { rawSize: -1 });
  const logs = [];
  await assert.rejects(
    () => forwardEmail(mail, DEFAULT_ENV, dependencies(async () => response(200), logs)),
    /mail forwarding failed/,
  );
  assert.equal(mail.rejected.length, 0);
  assert.deepEqual(logs, ["mail forward failed"]);
});

test("API base URL only permits HTTPS remotely and loopback HTTP for local smoke", () => {
  assert.equal(
    validateApiBaseUrl("https://api.example.test"),
    "https://api.example.test/api/v1/mail/receive",
  );
  assert.equal(
    validateApiBaseUrl("http://127.0.0.1:8000"),
    "http://127.0.0.1:8000/api/v1/mail/receive",
  );
  assert.equal(
    validateApiBaseUrl("http://localhost:8000"),
    "http://localhost:8000/api/v1/mail/receive",
  );
  assert.equal(
    validateApiBaseUrl("http://[::1]:8000"),
    "http://[::1]:8000/api/v1/mail/receive",
  );
  assert.throws(
    () => validateApiBaseUrl("http://api.example.test"),
    /invalid_api_base_url/,
  );
});

test("only API 413 and 422 cause permanent rejection", async () => {
  for (const status of [413, 422]) {
    const body = encoder.encode("mail-" + status);
    const mail = messageFromBytes(body);
    const logs = [];
    const result = await forwardEmail(
      mail,
      DEFAULT_ENV,
      dependencies(async () => response(status, { rawServerBody: "must-not-log" }), logs),
    );
    assert.equal(result.rejected, true);
    assert.equal(mail.rejected.length, 1);
    assert.deepEqual(logs, []);
  }
});

test("401, 429, 500, and transport failures throw without calling setReject or leaking sensitive values", async () => {
  const sensitiveBody = "private-mail-body-not-for-logs";
  const secret = DEFAULT_ENV.MAIL_HMAC_KEY;
  const responseBody = "private-downstream-response";
  for (const status of [401, 429, 500]) {
    const body = encoder.encode(sensitiveBody);
    const mail = messageFromBytes(body);
    const logs = [];
    await assert.rejects(
      () => forwardEmail(
        mail,
        DEFAULT_ENV,
        dependencies(async () => response(status, { detail: responseBody }), logs),
      ),
      /mail forwarding failed/,
    );
    assert.equal(mail.rejected.length, 0);
    assert.deepEqual(logs, ["mail forward failed"]);
    assert.equal(logs.join("\n").includes(sensitiveBody), false);
    assert.equal(logs.join("\n").includes(secret), false);
    assert.equal(logs.join("\n").includes(responseBody), false);
    assert.equal(logs.join("\n").includes("x-mail-signature"), false);
  }

  const body = encoder.encode(sensitiveBody);
  const mail = messageFromBytes(body);
  const logs = [];
  await assert.rejects(
    () => forwardEmail(
      mail,
      DEFAULT_ENV,
      dependencies(async () => {
        throw new Error(responseBody);
      }, logs),
    ),
    /mail forwarding failed/,
  );
  assert.equal(mail.rejected.length, 0);
  assert.deepEqual(logs, ["mail forward failed"]);
});

test("mail smoke keeps receipt identifiers in an exclusive private temp file and stdout-safe result", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "starisle-mail-smoke-test-"));
  const receiptPath = path.join(temporary, "receipt.json");
  const privateId = "PRIVATE_CANARY_MESSAGE_ID";
  const server = createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      response.writeHead(202, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ id: privateId }));
    });
  });
  const previousKey = process.env.MAIL_HMAC_KEY;
  try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert.ok(address && typeof address === "object");
    process.env.MAIL_HMAC_KEY = "mail-smoke-test-key";
    const result = await runSmoke([
      "http://127.0.0.1:" + address.port,
      "--receipt-file",
      receiptPath,
    ]);
    assert.deepEqual(Object.keys(result).sort(), ["elapsedMs", "receiptWritten", "status"]);
    assert.equal(result.status, 202);
    assert.equal(result.receiptWritten, true);
    assert.equal(JSON.stringify(result).includes(privateId), false);

    const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
    assert.equal(receipt.id, privateId);
    assert.equal(typeof receipt.subject, "string");
    assert.equal(typeof receipt.messageId, "string");
    await assert.rejects(() => writePrivateReceipt(receiptPath, receipt));
    await assert.rejects(() => privateReceiptPath(path.join(process.cwd(), "synthetic-receipt.json")));
  } finally {
    if (previousKey === undefined) delete process.env.MAIL_HMAC_KEY;
    else process.env.MAIL_HMAC_KEY = previousKey;
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});

test("mail smoke receipt rejects a symlinked configured system temp root", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "starisle-mail-smoke-link-test-"));
  const alias = path.join(os.tmpdir(), "starisle-mail-smoke-link-" + Date.now() + "-" + Math.random().toString(16).slice(2));
  try {
    await symlink(temporary, alias, process.platform === "win32" ? "junction" : "dir");
    await assert.rejects(() => privateReceiptPath(path.join(alias, "receipt.json"), alias));
  } finally {
    await unlink(alias).catch(() => {});
    await rm(temporary, { recursive: true, force: true });
  }
});

test("private receipt never enters either distinct live data root and never overwrites", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "starisle-mail-smoke-protected-test-"));
  const dataRoot = path.join(temporary, "data-root");
  const hostRoot = path.join(temporary, "data-host-root");
  const safeRoot = path.join(temporary, "safe");
  const previousData = process.env.DATA_ROOT;
  const previousHost = process.env.DATA_HOST_ROOT;
  try {
    await Promise.all([mkdir(dataRoot), mkdir(hostRoot), mkdir(safeRoot)]);
    await writeFile(path.join(hostRoot, "PRIVATE_CANARY_MARKER"), "PRIVATE_CANARY_DOCUMENT", "utf8");
    process.env.DATA_ROOT = dataRoot;
    process.env.DATA_HOST_ROOT = hostRoot;
    await assert.rejects(() => privateReceiptPath(path.join(dataRoot, "receipt.json")));
    await assert.rejects(() => writePrivateReceipt(path.join(hostRoot, "receipt.json"), { id: "private" }));
    const existing = path.join(safeRoot, "existing.json");
    await writeFile(existing, "PRIVATE_CANARY_EXISTING", "utf8");
    await assert.rejects(() => writePrivateReceipt(existing, { id: "replacement" }));
    assert.equal(await readFile(existing, "utf8"), "PRIVATE_CANARY_EXISTING");
    assert.equal(await readFile(path.join(hostRoot, "PRIVATE_CANARY_MARKER"), "utf8"), "PRIVATE_CANARY_DOCUMENT");
  } finally {
    if (previousData === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousData;
    if (previousHost === undefined) delete process.env.DATA_HOST_ROOT;
    else process.env.DATA_HOST_ROOT = previousHost;
    await rm(temporary, { recursive: true, force: true });
  }
});

test("private receipt rejects a reparse live data root before writing", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "starisle-mail-smoke-data-link-test-"));
  const safeRoot = path.join(temporary, "safe");
  const dataReal = path.join(temporary, "data-real");
  const dataAlias = path.join(temporary, "data-alias");
  const previousData = process.env.DATA_ROOT;
  const previousHost = process.env.DATA_HOST_ROOT;
  try {
    await Promise.all([mkdir(safeRoot), mkdir(dataReal)]);
    await symlink(dataReal, dataAlias, process.platform === "win32" ? "junction" : "dir");
    process.env.DATA_ROOT = dataAlias;
    process.env.DATA_HOST_ROOT = "";
    await assert.rejects(() => privateReceiptPath(path.join(safeRoot, "receipt.json")));
  } finally {
    if (previousData === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previousData;
    if (previousHost === undefined) delete process.env.DATA_HOST_ROOT;
    else process.env.DATA_HOST_ROOT = previousHost;
    await unlink(dataAlias).catch(() => {});
    await rm(temporary, { recursive: true, force: true });
  }
});

test("standalone mail smoke emits only a fixed failure when test credentials are absent", () => {
  const smokeScript = fileURLToPath(new URL("./email-worker-smoke.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [smokeScript], {
    encoding: "utf8",
    env: { ...process.env, MAIL_HMAC_KEY: "" },
  });
  assert.equal(result.status, 1);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr, "mail smoke failed\n");
  assert.equal(result.stdout.includes("PRIVATE_CANARY_SUBJECT"), false);
});
