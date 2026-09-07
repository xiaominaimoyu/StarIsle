import { describe, expect, it } from "vitest";
import type { PhotoSummary } from "@star-isle/contracts";
import {
  appendUniquePhotos,
  groupPhotosByCaptureDay,
  normalizePhotoCaptureAt,
  UNKNOWN_CAPTURE_DAY,
} from "./photo-timeline-state";

function photo(id: string, captureAt: string | null): PhotoSummary {
  return {
    id,
    thumbnailUrl: `/api/v1/photos/${id}/thumbnail`,
    captureAt,
    width: 100,
    height: 100,
  };
}

describe("photo timeline state", () => {
  it("groups legacy EXIF values by day and keeps unknown values together", () => {
    const groups = groupPhotosByCaptureDay([
      photo("morning", "2026:09:05 08:00:00"),
      photo("evening", "2026-09-05T18:30:00"),
      photo("missing", null),
      photo("invalid", "not-an-exif-time"),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups[0][1].map((item) => item.id)).toEqual(["morning", "evening"]);
    expect(groups[1]).toEqual([
      UNKNOWN_CAPTURE_DAY,
      [expect.objectContaining({ id: "missing" }), expect.objectContaining({ id: "invalid" })],
    ]);
  });

  it("deduplicates overlapping cursor pages without replacing existing objects", () => {
    const existing = photo("photo-002", "2026:09:05 10:00:00");
    const merged = appendUniquePhotos(
      [photo("photo-003", "2026:09:06 10:00:00"), existing],
      [photo("photo-002", "2026:09:05 10:00:00"), photo("photo-001", null)],
    );

    expect(merged.map((item) => item.id)).toEqual(["photo-003", "photo-002", "photo-001"]);
    expect(merged[1]).toBe(existing);
  });

  it("validates floating wall-clock fields without applying the browser timezone", () => {
    expect(normalizePhotoCaptureAt("2024:02:29 23:59:59")).toBe("2024-02-29T23:59:59");
    expect(normalizePhotoCaptureAt("2026-03-08T02:30:00")).toBe("2026-03-08T02:30:00");
    expect(normalizePhotoCaptureAt("2023:02:29 10:00:00")).toBeNull();
    expect(normalizePhotoCaptureAt("2026:09:05 24:00:00")).toBeNull();
  });
});
