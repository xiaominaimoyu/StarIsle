import { describe, expect, it, vi } from "vitest";
import type { MailSummary } from "@star-isle/contracts";

vi.mock("@/lib/api/modules", () => ({}));
vi.mock("@/stores/session", () => ({}));
vi.mock("@/lib/format", () => ({ formatRelativeTime: vi.fn() }));
vi.mock("@/components/ui/EmptyState", () => ({ default: vi.fn(() => null) }));
vi.mock("@/components/ui/StatusBanner", () => ({ default: vi.fn(() => null) }));

import {
  canApplyMailDetailResponse,
  canApplyMailListResponse,
  canRollbackMailRead,
  mergeMailListPage,
  shouldPreserveMailRead,
} from "./MailInbox";

function mail(id: string, read = false): MailSummary {
  return {
    id,
    from: `${id}@example.com`,
    subject: id,
    receivedAt: "2026-09-07T10:00:00+00:00",
    read,
  };
}

describe("mail inbox list state", () => {
  it("keeps refresh and load-more results stable regardless of response order", () => {
    const existingSecond = mail("mail-002");
    const initial = [mail("mail-003"), existingSecond];
    const refreshedThird = mail("mail-003", true);
    const refreshedPage = [mail("mail-004"), refreshedThird];
    // Keyset pages exclude their cursor anchor: mail-002 must survive even though
    // it is absent from both the refreshed head and the older response.
    const olderPage = [mail("mail-001")];

    const appendThenRefresh = mergeMailListPage(
      mergeMailListPage(initial, olderPage, "append"),
      refreshedPage,
      "refresh",
    );
    const refreshThenAppend = mergeMailListPage(
      mergeMailListPage(initial, refreshedPage, "refresh"),
      olderPage,
      "append",
    );

    for (const result of [appendThenRefresh, refreshThenAppend]) {
      expect(result.map((item) => item.id)).toEqual([
        "mail-004",
        "mail-003",
        "mail-002",
        "mail-001",
      ]);
      expect(result[1]).toBe(refreshedThird);
      expect(result[2]).toBe(existingSecond);
    }
  });

  it("keeps rows shifted behind a refreshed first page", () => {
    const refreshed = mergeMailListPage(
      [mail("mail-003"), mail("mail-002")],
      [mail("mail-004"), mail("mail-003")],
      "refresh",
    );

    expect(refreshed.map((item) => item.id)).toEqual(["mail-004", "mail-003", "mail-002"]);
  });

  it("preserves a newer local read state against an older list response", () => {
    const refreshed = mergeMailListPage(
      [mail("mail-003", true), mail("mail-002")],
      [mail("mail-004"), mail("mail-003", false)],
      "refresh",
      new Set(["mail-003"]),
    );

    expect(refreshed.map((item) => [item.id, item.read])).toEqual([
      ["mail-004", false],
      ["mail-003", true],
      ["mail-002", false],
    ]);
  });

  it("ignores responses from an old lifecycle or superseded refresh", () => {
    expect(canApplyMailListResponse(4, 4, 2, 2)).toBe(true);
    expect(canApplyMailListResponse(3, 4, 2, 2)).toBe(false);
    expect(canApplyMailListResponse(4, 4, 1, 2)).toBe(false);
    // 游标页采用函数式合并，同一挂载周期内不会被较新的首屏刷新作废。
    expect(canApplyMailListResponse(4, 4, null, 2)).toBe(true);
  });

  it("rejects stale detail responses after selecting another message", () => {
    expect(canApplyMailDetailResponse(4, 4, 2, 2)).toBe(true);
    expect(canApplyMailDetailResponse(3, 4, 2, 2)).toBe(false);
    expect(canApplyMailDetailResponse(4, 4, 1, 2)).toBe(false);
  });

  it("does not let an old or superseded read failure roll back a newer success", () => {
    expect(canRollbackMailRead(4, 4, 2, 2, false)).toBe(true);
    expect(canRollbackMailRead(3, 4, 2, 2, false)).toBe(false);
    expect(canRollbackMailRead(4, 4, 1, 2, false)).toBe(false);
    expect(canRollbackMailRead(4, 4, 2, 2, true)).toBe(false);
  });

  it("preserves optimistic read state while the matching mutation is still in flight", () => {
    expect(shouldPreserveMailRead(2, 2, true)).toBe(true);
    expect(shouldPreserveMailRead(3, 2, false)).toBe(true);
    expect(shouldPreserveMailRead(2, 2, false)).toBe(false);
  });
});
