import assert from "node:assert/strict";
import test from "node:test";

import {
  authorizedPull,
  createMemoryStore,
  parseAcknowledgeIds,
  parseVisitorMessage,
  pullVisitorMessages,
  submitVisitorMessage,
} from "./visitor-messages.js";

const KEY = "desk-pull-key-0123456789abcdef";

function request(body, headers = {}) {
  const text = JSON.stringify(body);
  return {
    headers: new Headers(headers),
    text: async () => text,
  };
}

function passingTurnstile() {
  return async () => ({ ok: true, json: async () => ({ success: true }) });
}

test("rejects oversized, empty, and control-character notes", () => {
  assert.equal(parseVisitorMessage(null), null);
  assert.equal(parseVisitorMessage({ name: "星", contact: "ab", body: "你好", turnstileToken: "x".repeat(20) }), null);
  const cleaned = parseVisitorMessage({ name: "a\u0000b", contact: "star@isle.test", body: "你好", turnstileToken: "x".repeat(20) });
  assert.equal(cleaned.name, "ab");
  const parsed = parseVisitorMessage({
    name: "  星屿  ",
    contact: "star@isle.test",
    body: "夜里好。",
    turnstileToken: "token-value-012345",
  });
  assert.equal(parsed.name, "星屿");
  assert.equal(parsed.body, "夜里好。");
});

test("submit stores nothing when turnstile or the rate limit fails", async () => {
  const store = createMemoryStore();
  const denied = await submitVisitorMessage({
    request: request({ name: "星", contact: "star@isle.test", body: "你好呀", turnstileToken: "token-value-012345" }),
    store,
    env: { TURNSTILE_SECRET_KEY: "secret" },
    fetchImpl: async () => ({ ok: true, json: async () => ({ success: false }) }),
    now: () => 1_000,
    randomUUID: () => "11111111-1111-4111-8111-111111111111",
    ip: "203.0.113.8",
  });
  assert.equal(denied.status, 403);
  assert.deepEqual(await store.takeOldest(10), []);

  const limited = createMemoryStore();
  for (let index = 0; index < 5; index += 1) {
    const response = await submitVisitorMessage({
      request: request({ name: "星", contact: "star@isle.test", body: `第${index}封`, turnstileToken: "token-value-012345" }),
      store: limited,
      env: { TURNSTILE_SECRET_KEY: "secret" },
      fetchImpl: passingTurnstile(),
      now: () => 1_000,
      randomUUID: () => `11111111-1111-4111-8111-11111111111${index}`,
      ip: "203.0.113.8",
    });
    assert.equal(response.status, 201);
  }
  const blocked = await submitVisitorMessage({
    request: request({ name: "星", contact: "star@isle.test", body: "太多了", turnstileToken: "token-value-012345" }),
    store: limited,
    env: { TURNSTILE_SECRET_KEY: "secret" },
    fetchImpl: passingTurnstile(),
    now: () => 1_000,
    randomUUID: () => "11111111-1111-4111-8111-111111111119",
    ip: "203.0.113.8",
  });
  assert.equal(blocked.status, 429);
  assert.equal((await limited.takeOldest(10)).length, 5);
});

test("pull key can only fetch and acknowledge, then the cloud copy is removed", async () => {
  const store = createMemoryStore();
  await store.insertMessage({
    id: "11111111-1111-4111-8111-111111111111",
    createdAt: "2026-11-10T00:00:00.000Z",
    name: "星",
    contact: "star@isle.test",
    body: "第一封",
  });
  const anonymous = await pullVisitorMessages({
    request: request({}),
    store,
    env: { VISITOR_PULL_KEY: KEY },
  });
  assert.equal(anonymous.status, 401);
  assert.equal((await store.takeOldest(10)).length, 1);
  assert.equal(authorizedPull(`Bearer ${KEY}`, KEY), true);
  assert.equal(authorizedPull("Bearer wrong-key-0123456789abcdef", KEY), false);

  const pulled = await pullVisitorMessages({
    request: request({}, { authorization: `Bearer ${KEY}` }),
    store,
    env: { VISITOR_PULL_KEY: KEY },
  });
  assert.equal(pulled.status, 200);
  const payload = await pulled.json();
  assert.equal(payload.messages.length, 1);
  const acked = await pullVisitorMessages({
    request: request(
      { acknowledge: [payload.messages[0].id] },
      { authorization: `Bearer ${KEY}` },
    ),
    store,
    env: { VISITOR_PULL_KEY: KEY },
  });
  const after = await acked.json();
  assert.equal(after.removed, 1);
  assert.deepEqual(after.messages, []);
  assert.equal(parseAcknowledgeIds({ acknowledge: ["not-a-uuid"] }), null);
});
