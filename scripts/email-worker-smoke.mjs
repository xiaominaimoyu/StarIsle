import { randomUUID } from "node:crypto";
import { lstat, open, realpath } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { forwardEmail } from "./email-worker.js";

const DEFAULT_MAX_BYTES = String(5 * 1024 * 1024);
const NETWORK_TIMEOUT_MS = 20_000;

function loopbackOrigin(value) {
  const parsed = new URL(value);
  const hostname = parsed.hostname.toLowerCase();
  if (
    !["http:", "https:"].includes(parsed.protocol)
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || parsed.username
    || parsed.password
    || !["127.0.0.1", "localhost", "::1", "[::1]"].includes(hostname)
  ) {
    throw new Error("invalid loopback API base");
  }
  return parsed.origin;
}

function parseArgs(args) {
  let apiBase = "http://127.0.0.1:8000";
  let receiptFile = null;
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
    if (value === "--receipt-file") {
      if (receiptFile !== null || index + 1 >= args.length) throw new Error("invalid receipt option");
      receiptFile = args[index + 1];
      index += 1;
      continue;
    }
    if (value.startsWith("-") || apiBase !== "http://127.0.0.1:8000") throw new Error("invalid smoke arguments");
    apiBase = value;
  }
  return { apiBase: loopbackOrigin(apiBase), receiptFile };
}

function smokeMime(subject, messageId) {
  const boundary = "m2-" + randomUUID().replaceAll("-", "");
  return [
    "From: sender@local.test",
    "To: private-inbox@local.test",
    "Subject: " + subject,
    "Message-ID: " + messageId,
    "MIME-Version: 1.0",
    'Content-Type: multipart/alternative; boundary="' + boundary + '"',
    "",
    "--" + boundary,
    'Content-Type: text/plain; charset="utf-8"',
    "",
    "M2 local loopback smoke: safe plain-text body.",
    "--" + boundary,
    'Content-Type: text/html; charset="utf-8"',
    "",
    '<p>M2 local smoke HTML.</p><script>window.bad=1</script><form action="https://bad.test"><input></form><a href="javascript:alert(1)">bad link</a><img src="https://tracker.invalid/pixel.gif" onerror="window.bad=2">',
    "--" + boundary + "--",
    "",
  ].join("\r\n");
}

function isWithin(candidate, parent) {
  const relative = path.relative(parent, candidate);
  return relative === "" || (!relative.startsWith(".." + path.sep) && relative !== ".." && !path.isAbsolute(relative));
}

function sameResolvedPath(left, right) {
  return path.relative(left, right) === "" && path.relative(right, left) === "";
}

async function receiptProtectedRoots() {
  const roots = [];
  for (const raw of [process.env.DATA_ROOT, process.env.DATA_HOST_ROOT]) {
    if (typeof raw !== "string" || !raw.trim()) continue;
    const lexical = path.resolve(raw);
    let resolved;
    try {
      resolved = await realpath(lexical);
    } catch {
      throw new Error("invalid receipt protected root");
    }
    if (!sameResolvedPath(lexical, resolved)) {
      throw new Error("invalid receipt protected root");
    }
    roots.push(resolved);
  }
  return roots;
}

export async function privateReceiptPath(value, configuredTemp = os.tmpdir()) {
  if (typeof value !== "string" || !value) throw new Error("invalid receipt path");
  const candidate = path.resolve(value);
  const parent = path.dirname(candidate);
  const lexicalTemp = path.resolve(configuredTemp);
  const lexicalParent = path.resolve(parent);
  const [realParent, realTemp] = await Promise.all([realpath(parent), realpath(lexicalTemp)]);
  if (
    !sameResolvedPath(lexicalTemp, realTemp)
    || !sameResolvedPath(lexicalParent, realParent)
    || !isWithin(realParent, realTemp)
    || !sameResolvedPath(path.dirname(candidate), realParent)
  ) {
    throw new Error("receipt path is outside system temp");
  }
  try {
    await lstat(candidate);
    throw new Error("receipt path already exists");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const resolvedCandidate = path.join(realParent, path.basename(candidate));
  for (const root of await receiptProtectedRoots()) {
    if (isWithin(resolvedCandidate, root)) {
      throw new Error("receipt path is inside protected root");
    }
  }
  return candidate;
}

export async function writePrivateReceipt(value, receipt) {
  const receiptPath = await privateReceiptPath(value);
  const handle = await open(receiptPath, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify(receipt) + "\n", "utf8");
  } finally {
    await handle.close();
  }
}

async function boundedFetch(url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), NETWORK_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function runSmoke(args = process.argv.slice(2)) {
  const { apiBase, receiptFile } = parseArgs(args);
  const hmacKey = process.env.MAIL_HMAC_KEY;
  if (typeof hmacKey !== "string" || !hmacKey.trim()) throw new Error("missing smoke HMAC key");

  const subject = "M2 local mail smoke " + randomUUID().slice(0, 8);
  const messageId = "<m2-smoke-" + randomUUID() + "@local.test>";
  const body = new TextEncoder().encode(smokeMime(subject, messageId));
  const rejected = [];
  const message = {
    rawSize: body.byteLength,
    raw: new ReadableStream({
      start(controller) {
        controller.enqueue(body);
        controller.close();
      },
    }),
    setReject(reason) {
      rejected.push(reason);
    },
  };

  const startedAt = Date.now();
  const result = await forwardEmail(
    message,
    {
      API_BASE_URL: apiBase,
      MAIL_HMAC_KEY: hmacKey,
      MAIL_MAX_BYTES: process.env.MAIL_MAX_BYTES || DEFAULT_MAX_BYTES,
    },
    { fetchImpl: boundedFetch, log: () => {} },
  );
  const elapsedMs = Date.now() - startedAt;
  if (result.rejected || rejected.length || !Number.isInteger(result.status)) {
    throw new Error("mail worker did not receive a local receipt");
  }

  if (receiptFile !== null) {
    if (typeof result.id !== "string" || !result.id) throw new Error("mail worker receipt lacks identifier");
    await writePrivateReceipt(receiptFile, {
      id: result.id,
      subject,
      messageId,
      status: result.status,
      elapsedMs,
    });
  }
  return { status: result.status, elapsedMs, receiptWritten: receiptFile !== null };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  runSmoke().then(
    (result) => {
      console.log(JSON.stringify(result));
    },
    () => {
      console.error("mail smoke failed");
      process.exitCode = 1;
    },
  );
}
