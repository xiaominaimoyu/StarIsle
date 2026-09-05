import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatDateTime,
  formatRelativeTime,
  parseDateInput,
} from "./format";

describe("EXIF date formatting", () => {
  it("treats DateTimeOriginal as a valid local wall-clock value", () => {
    const exif = "2026:09:05 10:02:03";
    const canonical = "2026-09-05T10:02:03";
    const local = new Date(2026, 8, 5, 10, 2, 3);

    expect(parseDateInput(exif)?.getTime()).toBe(local.getTime());
    expect(parseDateInput(canonical)?.getTime()).toBe(local.getTime());
    expect(formatDate(exif)).toBe(formatDate(local));
    expect(formatDate(canonical)).toBe(formatDate(local));
    expect(formatDateTime(exif)).toBe(formatDateTime(local));
  });

  it("rejects impossible EXIF fields instead of normalizing into another day", () => {
    const invalid = "2026:02:31 10:02:03";

    expect(parseDateInput(invalid)).toBeNull();
    expect(parseDateInput("2026-02-31T10:02:03")).toBeNull();
    expect(formatDate(invalid)).toBe(invalid);
    expect(formatRelativeTime(invalid)).toBe(invalid);
  });
});
