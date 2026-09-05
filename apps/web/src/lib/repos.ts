import type { Repo, RepoSnapshot } from "@star-isle/contracts";
import bundledSnapshot from "@/site-data/repos.json";

/** 构建期仓库 JSON 的加载器(仅静态构建期数据,运行时零外网请求) */

export type { Repo, RepoSnapshot };

export interface RepoFilter {
  source: "all" | "github" | "gitee";
  sort: "featured" | "updated";
}

export function loadRepoSnapshot(): RepoSnapshot {
  // 受版本控制的构建期快照；干净检出无需依赖被忽略的运行时 data/。
  return bundledSnapshot as RepoSnapshot;
}

export function filterRepos(
  snapshot: RepoSnapshot,
  filter: RepoFilter,
): Repo[] {
  let list = snapshot.repos;
  if (filter.source !== "all") {
    list = list.filter((r) => r.source === filter.source);
  }
  const sorted = [...list];
  if (filter.sort === "featured") {
    sorted.sort((a, b) => {
      if (a.featured !== b.featured) return a.featured ? -1 : 1;
      return b.updatedAt.localeCompare(a.updatedAt);
    });
  } else {
    sorted.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }
  return sorted;
}

export function featuredRepos(snapshot: RepoSnapshot): Repo[] {
  return snapshot.repos
    .filter((r) => r.featured)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
