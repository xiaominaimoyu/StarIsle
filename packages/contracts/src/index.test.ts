import { describe, expect, it } from "vitest";
import type {
  ConversationDetail,
  ConversationListResponse,
  CursorPage,
  JobStatus,
  JobListResponse,
  KnowledgeDocumentListResponse,
  StorageStats,
  RagStreamEvent,
  Repo,
  RepoSnapshot,
} from "./index";

describe("contracts", () => {
  it("Repo 统一 schema 归一化两来源字段", () => {
    const repo: Repo = {
      source: "github",
      name: "star-isle",
      description: "个人门户",
      url: "https://github.com/x/star-isle",
      language: "TypeScript",
      stars: 12,
      forks: 3,
      updatedAt: "2026-08-01T00:00:00Z",
      featured: true,
      topics: ["astro"],
    };
    expect(repo.featured).toBe(true);
    expect(repo.source).toBe("github");
  });

  it("RepoSnapshot 记录来源失败状态而非虚构数据", () => {
    const snapshot: RepoSnapshot = {
      repos: [],
      fetchedAt: "2026-08-27T00:00:00Z",
      sources: [{ source: "github", ok: false, error: "429 rate limited" }],
    };
    expect(snapshot.repos).toHaveLength(0);
    expect(snapshot.sources[0]?.ok).toBe(false);
  });

  it("CursorPage 支持无 nextCursor 收尾", () => {
    const page: CursorPage<number> = { items: [1, 2], nextCursor: null };
    expect(page.nextCursor).toBeNull();
  });

  it("会话列表和详情共用游标分页外形", () => {
    const list: ConversationListResponse = {
      items: [{ id: "conv-1", title: "会话", createdAt: "2026-09-04T00:00:00+00:00" }],
      nextCursor: "cursor-next",
    };
    const detail: ConversationDetail = {
      id: "conv-1",
      title: "会话",
      createdAt: "2026-09-04T00:00:00+00:00",
      messages: [],
      nextCursor: null,
    };

    expect(list.nextCursor).toBe("cursor-next");
    expect(detail.nextCursor).toBeNull();
  });

  it("知识库列表使用共享游标分页契约", () => {
    const page: KnowledgeDocumentListResponse = {
      items: [],
      nextCursor: null,
    };
    expect(page.nextCursor).toBeNull();
  });

  it("JobStatus 五态枚举完整", () => {
    const states: JobStatus["status"][] = [
      "queued",
      "running",
      "succeeded",
      "failed",
      "cancelled",
    ];
    expect(states).toHaveLength(5);
  });

  it("作业与存储概览类型不含 payload 或对象路径", () => {
    const jobs: JobListResponse = {
      items: [{ jobId: "job-1", jobType: "ingest", status: "queued", progress: null, errorCode: null, updatedAt: "2026-09-04T00:00:00Z", documentId: "doc-1" }],
      counts: { queued: 1, running: 0, succeeded: 0, failed: 0, cancelled: 0 },
    };
    const storage: StorageStats = { usedBytes: 42, fileCount: 1, photoCount: 1 };
    expect(jobs.items[0]).not.toHaveProperty("payload");
    expect(storage.usedBytes).toBe(42);
  });

  it("RAG 流事件覆盖 stage/heartbeat/token/citation/error/done", () => {
    const events: RagStreamEvent[] = [
      { event: "stage", stage: "retrieving" },
      { event: "heartbeat", ts: 1 },
      { event: "token", token: "你" },
      { event: "citation", citation: {
        documentId: "d1",
        documentName: "手册",
        version: 1,
        chunkId: "c1",
        snippet: "片段",
      } },
      { event: "error", code: "INTERNAL", message: "失败" },
      { event: "done", messageId: "m1", citations: [] },
    ];
    expect(events.map((e) => e.event)).toEqual([
      "stage",
      "heartbeat",
      "token",
      "citation",
      "error",
      "done",
    ]);
  });
});
