import { describe, expect, it } from "vitest";
import type { KnowledgeDocument } from "@star-isle/contracts";
import { appendUniqueDocuments } from "./document-pagination";

function document(id: string): KnowledgeDocument {
  return {
    id,
    title: id,
    documentType: "txt",
    version: 1,
    hash: "hash",
    indexState: "ready",
    indexErrorCode: null,
    createdAt: "2026-09-05T10:00:00+00:00",
  };
}

describe("appendUniqueDocuments", () => {
  it("appends a later cursor page without duplicate IDs or replacing local state", () => {
    const original = document("doc-002");
    const merged = appendUniqueDocuments(
      [document("doc-003"), original],
      [document("doc-002"), document("doc-001")],
    );

    expect(merged.map((item) => item.id)).toEqual(["doc-003", "doc-002", "doc-001"]);
    expect(merged[1]).toBe(original);
  });
});
