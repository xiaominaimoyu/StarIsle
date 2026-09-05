import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelJob, deleteDocument, fetchConversation, fetchStorageStats, listConversations, listJobs, listPhotos, ragQuery } from "./modules";

const encoder = new TextEncoder();

function sseResponse(chunks: string[]): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

const callbacks = () => ({
  onStage: vi.fn(),
  onHeartbeat: vi.fn(),
  onToken: vi.fn(),
  onCitation: vi.fn(),
  onError: vi.fn(),
});

afterEach(() => vi.unstubAllGlobals());

describe("ragQuery SSE consumption", () => {
  it("removes data prefix and combines arbitrarily split successful events", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"event":"stage","stage":"retrieving"}\n\n',
      'data: {"event":"heartbeat","ts":123}\n\n',
      'data: {"event":"token","token":"星",',
      '"ignored":true}\n\n\r\ndata: {"event":"citation","citation":{"documentId":"d","documentName":"文档","version":1,"chunkId":"c","snippet":"片段"}}\n\n',
      'data: {"event":"done","messageId":"m1","citations":[]}\n\n',
    ])));
    const cb = callbacks();

    const result = await ragQuery(undefined, "问题", "csrf", new AbortController().signal, cb);

    expect(result).toEqual({ messageId: "m1", citations: [], content: "星" });
    expect(cb.onStage).toHaveBeenCalledWith("retrieving");
    expect(cb.onHeartbeat).toHaveBeenCalledWith(123);
    expect(cb.onToken).toHaveBeenCalledWith("星");
    expect(cb.onCitation).toHaveBeenCalledTimes(1);
  });

  it("rejects on an SSE error and never returns a completed answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"event":"token","token":"partial"}\n\n',
      'data: {"event":"error","code":"INTERNAL","message":"生成失败"}\n\n',
    ])));
    const cb = callbacks();

    await expect(ragQuery(undefined, "问题", "csrf", new AbortController().signal, cb)).rejects.toThrow("生成失败");
    expect(cb.onError).toHaveBeenCalledWith("INTERNAL", "生成失败");
  });

  it("rejects a stream that ends without done", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"event":"token","token":"partial"}\n\n',
    ])));

    await expect(ragQuery(undefined, "问题", "csrf", new AbortController().signal, callbacks())).rejects.toThrow("完成前中断");
  });

  it.each([
    ["without tokens", ['data: {"event":"done","messageId":"m-empty","citations":[]}\n\n']],
    ["with only whitespace tokens", [
      'data: {"event":"token","token":"  \\n\\t"}\n\n',
      'data: {"event":"done","messageId":"m-whitespace","citations":[]}\n\n',
    ]],
  ])("rejects a done event %s", async (_caseName, chunks) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(chunks)));

    await expect(ragQuery(undefined, "问题", "csrf", new AbortController().signal, callbacks())).rejects.toThrow("有效内容");
  });
});

describe("photo pagination", () => {
  it("returns the CursorPage contract without a cursor", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], nextCursor: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listPhotos()).resolves.toEqual({ items: [], nextCursor: null });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/photos",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("passes an encoded cursor when listing the next page", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], nextCursor: "2026:01:01 10:00:00" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const cursor = "2026-09-03T10:00:00.123456+00:00";
    await listPhotos(cursor);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/photos?cursor=${encodeURIComponent(cursor)}`,
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });
});

describe("conversation pagination", () => {
  it("returns the shared first-page shapes", async () => {
    const listPage = {
      items: [{ id: "conv-1", title: "会话", createdAt: "2026-09-04T00:00:00+00:00" }],
      nextCursor: "list-next",
    };
    const detailPage = {
      id: "conv-1",
      title: "会话",
      createdAt: "2026-09-04T00:00:00+00:00",
      messages: [],
      nextCursor: "message-next",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(listPage), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(detailPage), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversations()).resolves.toEqual(listPage);
    await expect(fetchConversation("conv-1")).resolves.toEqual(detailPage);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/conversations",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/conversations/conv-1",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("encodes a next-page cursor for list and detail requests", async () => {
    const cursor = "v1+/游标=next";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], nextCursor: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "conv-1",
        title: "会话",
        createdAt: "2026-09-04T00:00:00+00:00",
        messages: [],
        nextCursor: null,
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await listConversations(cursor);
    await fetchConversation("conv-1", cursor);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/v1/conversations?cursor=${encodeURIComponent(cursor)}`,
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/v1/conversations/conv-1?cursor=${encodeURIComponent(cursor)}`,
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });
});

describe("document jobs", () => {
  it("keeps DELETE accepted response as a JobResponse", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ jobId: "job-delete" }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteDocument("document-1", "csrf-token")).resolves.toEqual({ jobId: "job-delete" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/knowledge/documents/document-1",
      expect.objectContaining({ method: "DELETE", credentials: "include" }),
    );
  });

  it("lists jobs with explicit query and cancels with CSRF", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], counts: { queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 } }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "cancelled", progress: null, errorCode: "JOB_CANCELLED", updatedAt: "2026-09-04T00:00:00Z" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listJobs(3, true)).resolves.toMatchObject({ items: [], counts: { running: 0 } });
    await expect(cancelJob("job-1", "csrf-token")).resolves.toMatchObject({ status: "cancelled" });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/jobs?limit=3&activeOnly=true",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/jobs/job-1/cancel",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    const requestOptions = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect((requestOptions.headers as Headers).get("X-CSRF-Token")).toBe("csrf-token");
  });

  it("fetches safe storage statistics", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ usedBytes: 12, fileCount: 1, photoCount: 2 }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStorageStats()).resolves.toEqual({ usedBytes: 12, fileCount: 1, photoCount: 2 });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/stats/storage", expect.objectContaining({ method: "GET", credentials: "include" }));
  });
});
