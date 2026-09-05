import type { PhotoSummary } from "@star-isle/contracts";
import { formatDate, parseDateInput } from "../../lib/format";

export const UNKNOWN_CAPTURE_DAY = "未知日期";

/** Keep overlapping cursor pages stable and preserve already-rendered objects. */
export function appendUniquePhotos(
  current: PhotoSummary[],
  incoming: PhotoSummary[],
): PhotoSummary[] {
  const ids = new Set(current.map((photo) => photo.id));
  return [...current, ...incoming.filter((photo) => {
    if (ids.has(photo.id)) return false;
    ids.add(photo.id);
    return true;
  })];
}

export function photoCaptureDayLabel(captureAt: string | null): string {
  if (!captureAt) return UNKNOWN_CAPTURE_DAY;
  const parsed = parseDateInput(captureAt);
  return parsed ? formatDate(parsed) : UNKNOWN_CAPTURE_DAY;
}

export function groupPhotosByCaptureDay(
  photos: PhotoSummary[],
): Array<[string, PhotoSummary[]]> {
  const groups = new Map<string, PhotoSummary[]>();
  for (const photo of photos) {
    const label = photoCaptureDayLabel(photo.captureAt);
    groups.set(label, [...(groups.get(label) ?? []), photo]);
  }
  return [...groups.entries()];
}
