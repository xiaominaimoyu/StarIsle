import { useMemo, useState } from "react";
import type { Repo, RepoSnapshot } from "@star-isle/contracts";
import { filterRepos } from "@/lib/repos";
import { formatDate } from "@/lib/format";
import EmptyState from "@/components/ui/EmptyState";

const languagePalette: Record<string, string> = {
  TypeScript: "#3178c6",
  JavaScript: "#f1e05a",
  Python: "#3572a5",
  HTML: "#e34c26",
  CSS: "#563d7c",
  Rust: "#dea584",
  Go: "#00add8",
  Java: "#b07219",
  Vue: "#41b883",
  Shell: "#89e051",
};

interface RepoGridProps {
  snapshot: RepoSnapshot;
}

type SourceFilter = "all" | "github" | "gitee";
type SortFilter = "featured" | "updated";

export default function RepoGrid({ snapshot }: RepoGridProps) {
  const [source, setSource] = useState<SourceFilter>("all");
  const [sort, setSort] = useState<SortFilter>("featured");

  const repos = useMemo(
    () => filterRepos(snapshot, { source, sort }),
    [snapshot, source, sort],
  );

  const sourcesOk = snapshot.sources.filter((s) => s.ok).length;
  const sourcesTotal = snapshot.sources.length;

  if (snapshot.sources.length === 0) {
    return (
      <EmptyState
        title="暂未配置仓库来源"
        description="构建时已跳过抓取。配置 GITHUB_USERNAME / GITEE_USERNAME 后重新构建即可展示。"
      />
    );
  }

  return (
    <section aria-label="仓库快照" className="flex flex-col gap-4">
      {sourcesOk < sourcesTotal ? (
        <div className="flex items-center gap-2 rounded-md border border-accent/40 bg-surface px-3 py-2 text-sm text-accent" role="status">
          <span aria-hidden="true">ℹ</span>
          <span>
            以下为构建期快照,采集于 {formatDate(snapshot.fetchedAt)};部分来源抓取失败,已显示最近成功缓存;最新数据请访问来源平台。
          </span>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="仓库筛选">
        <div className="flex overflow-hidden rounded-md border border-border" role="group" aria-label="按来源筛选">
          {(["all", "github", "gitee"] as SourceFilter[]).map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={source === s}
              className={`px-3 py-1.5 text-sm focus-ring ${
                source === s ? "bg-accent text-accent-foreground" : "bg-transparent text-text-dim hover:text-text"
              }`}
              onClick={() => setSource(s)}
            >
              {s === "all" ? "全部" : s === "github" ? "GitHub" : "Gitee"}
            </button>
          ))}
        </div>
        <div className="flex overflow-hidden rounded-md border border-border" role="group" aria-label="排序方式">
          {(["featured", "updated"] as SortFilter[]).map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={sort === s}
              className={`px-3 py-1.5 text-sm focus-ring ${
                sort === s ? "bg-accent text-accent-foreground" : "bg-transparent text-text-dim hover:text-text"
              }`}
              onClick={() => setSort(s)}
            >
              {s === "featured" ? "精选优先" : "最近更新"}
            </button>
          ))}
        </div>
      </div>

      {repos.length === 0 ? (
        <EmptyState
          title="该筛选下暂无仓库"
          description="换一个来源或排序方式试试;快照数据来自构建期抓取。"
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {repos.map((repo) => (
            <RepoCardInner key={`${repo.source}-${repo.name}`} repo={repo} />
          ))}
        </div>
      )}
    </section>
  );
}

function RepoCardInner({ repo }: { repo: Repo }) {
  return (
    <a
      href={repo.url}
      target="_blank"
      rel="noopener noreferrer"
      className="card card-hover group flex h-full flex-col gap-2 p-4 focus-ring"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="truncate font-semibold text-text group-hover:text-accent">{repo.name}</span>
        <div className="flex shrink-0 gap-1">
          {repo.featured ? (
            <span className="inline-flex items-center rounded-full border border-accent-warm/50 px-2 py-0.5 text-xs text-accent-warm">精选</span>
          ) : null}
          <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-xs text-text-dim">
            {repo.source === "github" ? "GitHub" : "Gitee"}
          </span>
        </div>
      </div>
      <p className="line-clamp-2 flex-1 text-sm text-text-dim">{repo.description ?? "暂无描述"}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-dim">
        {repo.language ? (
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block size-2.5 rounded-full"
              style={{ backgroundColor: languagePalette[repo.language] ?? "#9aa5c4" }}
            />
            <span>{repo.language}</span>
          </span>
        ) : null}
        <span aria-label={`${repo.stars} 星`}>★ {repo.stars}</span>
        <span aria-label={`${repo.forks} 分支`}>⑂ {repo.forks}</span>
        <span>更新于 {formatDate(repo.updatedAt)}</span>
      </div>
    </a>
  );
}
