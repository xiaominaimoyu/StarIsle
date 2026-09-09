import { describe, expect, it, vi } from "vitest";
import type { Citation } from "@star-isle/contracts";

vi.mock("@/lib/format", () => ({ formatRelativeTime: vi.fn() }));

import { citationRenderKey } from "./MessageBubble";

function citation(documentId: string, chunkId: string): Citation {
  return {
    documentId,
    documentName: documentId,
    version: 1,
    chunkId,
    snippet: "片段",
  };
}

describe("citationRenderKey", () => {
  it("is stable and unique when chunk IDs repeat within one message", () => {
    const citations = [
      citation("doc-a", "chunk-001"),
      citation("doc-b", "chunk-001"),
      citation("doc-a", "chunk-001"),
    ];
    const keys = citations.map(citationRenderKey);

    expect(new Set(keys).size).toBe(citations.length);
    expect(citations.map(citationRenderKey)).toEqual(keys);
  });
});
