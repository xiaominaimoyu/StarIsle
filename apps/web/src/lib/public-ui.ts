export type PublicTheme = "dark" | "light";

export interface PublicBackgroundSettings {
  url: string | null;
  custom: boolean;
  scrimOpacity: number;
  blur: number;
}

export type PublicRepoSource = "all" | "github" | "gitee";
export type PublicRepoSort = "featured" | "updated";

export interface PublicRepoLike {
  source: Exclude<PublicRepoSource, "all">;
  featured?: boolean;
  updatedAt?: string | null;
}

export function clampNumber(value: unknown, min: number, max: number, fallback: number): number {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(max, Math.max(min, number));
}

export function parsePublicTheme(value: unknown): PublicTheme {
  return value === "light" ? "light" : "dark";
}

export function nextPublicTheme(theme: PublicTheme): PublicTheme {
  return theme === "light" ? "dark" : "light";
}

/** Keeps the existing player contract: missing or malformed storage uses 0.8, while "0" remains muted. */
export function parsePublicPlayerVolume(raw: string | null): number {
  if (raw === null || raw.trim() === "") return 0.8;
  const volume = Number(raw);
  return Number.isFinite(volume) && volume >= 0 && volume <= 1 ? volume : 0.8;
}

export function parsePublicBackgroundSettings(raw: string | null): PublicBackgroundSettings {
  const defaults: PublicBackgroundSettings = {
    url: null,
    custom: false,
    scrimOpacity: 0.45,
    blur: 0,
  };

  if (!raw) return defaults;

  try {
    const parsed = JSON.parse(raw) as Partial<PublicBackgroundSettings>;
    return {
      // 上传背景的旧记录可能带有已失效的 blob: URL；恢复时始终从 IndexedDB 读取。
      url: typeof parsed.url === "string" && !parsed.custom ? parsed.url : null,
      custom: parsed.custom === true,
      scrimOpacity: clampNumber(parsed.scrimOpacity, 0, 0.9, defaults.scrimOpacity),
      blur: clampNumber(parsed.blur, 0, 20, defaults.blur),
    };
  } catch {
    return defaults;
  }
}

function timestamp(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * 返回稳定排序的副本：无效或缺失日期按最旧处理，同分时保持快照原有顺序。
 */
export function sortPublicRepos<T extends PublicRepoLike>(
  repos: readonly T[],
  filter: { source: PublicRepoSource; sort: PublicRepoSort },
): T[] {
  return repos
    .map((repo, index) => ({ repo, index }))
    .filter(({ repo }) => filter.source === "all" || repo.source === filter.source)
    .sort((left, right) => {
      if (filter.sort === "featured" && Boolean(left.repo.featured) !== Boolean(right.repo.featured)) {
        return left.repo.featured ? -1 : 1;
      }

      const dateDifference = timestamp(right.repo.updatedAt) - timestamp(left.repo.updatedAt);
      return dateDifference || left.index - right.index;
    })
    .map(({ repo }) => repo);
}
