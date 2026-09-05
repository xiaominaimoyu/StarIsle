import type { KnowledgeDocument } from "@star-isle/contracts";

/** Preserve the already-rendered row when an overlapping later page arrives. */
export function appendUniqueDocuments(
  current: KnowledgeDocument[],
  incoming: KnowledgeDocument[],
): KnowledgeDocument[] {
  const ids = new Set(current.map((document) => document.id));
  return [...current, ...incoming.filter((document) => {
    if (ids.has(document.id)) return false;
    ids.add(document.id);
    return true;
  })];
}
