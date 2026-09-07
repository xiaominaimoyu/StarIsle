import type { PhotoSummary } from "@star-isle/contracts";

export const UNKNOWN_CAPTURE_DAY = "未知日期";
const LEGACY_CAPTURE = /^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})$/;
const CANONICAL_CAPTURE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$/;

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

export function normalizePhotoCaptureAt(captureAt: string): string | null {
  const parts = LEGACY_CAPTURE.exec(captureAt) ?? CANONICAL_CAPTURE.exec(captureAt);
  if (!parts) return null;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = parts;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const daysInMonth = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    year < 1
    || month < 1
    || month > 12
    || day < 1
    || day > daysInMonth[month - 1]
    || hour > 23
    || minute > 59
    || second > 59
  ) {
    return null;
  }
  return `${yearText}-${monthText}-${dayText}T${hourText}:${minuteText}:${secondText}`;
}

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
  const normalized = normalizePhotoCaptureAt(captureAt);
  return normalized
    ? `${normalized.slice(0, 4)}/${normalized.slice(5, 7)}/${normalized.slice(8, 10)}`
    : UNKNOWN_CAPTURE_DAY;
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
