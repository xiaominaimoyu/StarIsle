/**
 * 星屿 StarIsle · 构建期仓库快照脚本
 *
 * 数据流:GitHub REST / Gitee OpenAPI 分页抓取 → 归一化 → 版本化 JSON 缓存 → Astro 静态页。
 * - 运行时(浏览器)不请求外网 API。
 * - GITHUB_TOKEN 仅来自 CI/服务端构建环境,绝不写入产物。
 * - 来源失败时保留最近成功缓存并记录来源状态,不虚构数据。
 *
 * 用法:
 *   GITHUB_USERNAME=xxx GITEE_USERNAME=yyy GITHUB_TOKEN=... pnpm fetch:repos
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Repo, RepoSnapshot } from "@star-isle/contracts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CACHE_DIR = resolve(ROOT, "data/cache/repos");
const CACHE_FILE = resolve(CACHE_DIR, "repos.json");
const WEB_DATA_FILE = resolve(ROOT, "apps/web/src/site-data/repos.json");
const FEATURED_FILE = resolve(ROOT, "apps/web/src/site-data/featured.json");

interface RawGitHubRepo {
  name: string;
  description: string | null;
  html_url: string;
  language: string | null;
  stargazers_count: number;
  forks_count: number;
  updated_at: string;
  topics: string[];
}

interface RawGiteeRepo {
  name: string;
  description: string | null;
  html_url: string;
  language: string | null;
  stargazers_count: number;
  forks_count: number;
  updated_at: string;
}

async function fetchAll<T>(url: string, token?: string): Promise<T[]> {
  const items: T[] = [];
  let page = 1;
  let perPage = 100;
  // GitHub 上限 100/页;Gitee 上限 100/页
  for (;;) {
    const sep = url.includes("?") ? "&" : "?";
    const pageUrl = `${url}${sep}page=${page}&per_page=${perPage}`;
    const headers: Record<string, string> = {
      Accept: "application/vnd.github+json",
      "User-Agent": "star-isle-builder",
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(pageUrl, { headers });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} ${res.statusText} @ ${pageUrl}`);
    }
    const batch = (await res.json()) as T[];
    items.push(...batch);
    if (batch.length < perPage) break;
    page += 1;
  }
  return items;
}

async function loadFeatured(): Promise<string[]> {
  try {
    const raw = await readFile(FEATURED_FILE, "utf8");
    const parsed = JSON.parse(raw) as { featured: string[] };
    return Array.isArray(parsed.featured) ? parsed.featured : [];
  } catch {
    return [];
  }
}

async function fetchGithub(username: string, token?: string): Promise<Repo[]> {
  const raw = await fetchAll<RawGitHubRepo>(
    `https://api.github.com/users/${username}/repos`,
    token,
  );
  return raw.map((r) => ({
    source: "github" as const,
    name: r.name,
    description: r.description,
    url: r.html_url,
    language: r.language,
    stars: r.stargazers_count,
    forks: r.forks_count,
    updatedAt: r.updated_at,
    featured: false,
    topics: r.topics ?? [],
  }));
}

async function fetchGitee(username: string): Promise<Repo[]> {
  const raw = await fetchAll<RawGiteeRepo>(
    `https://gitee.com/api/v5/users/${username}/repos`,
  );
  return raw.map((r) => ({
    source: "gitee" as const,
    name: r.name,
    description: r.description,
    url: r.html_url,
    language: r.language,
    stars: r.stargazers_count,
    forks: r.forks_count,
    updatedAt: r.updated_at,
    featured: false,
    topics: [],
  }));
}

async function loadLastCache(): Promise<RepoSnapshot | null> {
  try {
    const raw = await readFile(CACHE_FILE, "utf8");
    return JSON.parse(raw) as RepoSnapshot;
  } catch {
    return null;
  }
}

async function main(): Promise<void> {
  const githubUser = process.env.GITHUB_USERNAME?.trim() ?? "";
  const giteeUser = process.env.GITEE_USERNAME?.trim() ?? "";
  const githubToken = process.env.GITHUB_TOKEN?.trim() || undefined;

  const featured = await loadFeatured();
  const featuredSet = new Set(featured);

  const sources: RepoSnapshot["sources"] = [];
  let repos: Repo[] = [];

  if (githubUser) {
    try {
      const gh = await fetchGithub(githubUser, githubToken);
      repos.push(...gh);
      sources.push({ source: "github", ok: true });
      console.log(`[github] ${gh.length} repos`);
    } catch (err) {
      sources.push({
        source: "github",
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
      console.error(`[github] 抓取失败: ${sources.at(-1)?.error}`);
    }
  } else {
    console.log("[github] 未配置 GITHUB_USERNAME,跳过该来源");
  }

  if (giteeUser) {
    try {
      const ge = await fetchGitee(giteeUser);
      repos.push(...ge);
      sources.push({ source: "gitee", ok: true });
      console.log(`[gitee] ${ge.length} repos`);
    } catch (err) {
      sources.push({
        source: "gitee",
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
      console.error(`[gitee] 抓取失败: ${sources.at(-1)?.error}`);
    }
  } else {
    console.log("[gitee] 未配置 GITEE_USERNAME,跳过该来源");
  }

  // featured 只来自本地清单;抓取失败时回退最近成功缓存并保留其数据
  if (sources.some((s) => !s.ok)) {
    const last = await loadLastCache();
    if (last && last.repos.length > 0) {
      repos = last.repos;
      console.warn("[cache] 存在来源失败,已回退最近成功缓存");
    } else {
      console.warn("[cache] 无可用缓存,来源失败来源将显示为空态");
    }
  }

  const named = new Set(repos.map((r) => r.name));
  for (const f of featured) {
    if (!named.has(f)) {
      console.warn(`[featured] 清单中的 "${f}" 未在快照中找到,忽略`);
    }
  }
  for (const r of repos) r.featured = featuredSet.has(r.name);

  // featured 优先,其余按最近更新时间排序
  repos.sort((a, b) => {
    if (a.featured !== b.featured) return a.featured ? -1 : 1;
    return b.updatedAt.localeCompare(a.updatedAt);
  });

  const snapshot: RepoSnapshot = {
    repos,
    fetchedAt: new Date().toISOString(),
    sources,
  };

  await mkdir(CACHE_DIR, { recursive: true });
  await writeFile(CACHE_FILE, JSON.stringify(snapshot, null, 2), "utf8");
  await mkdir(dirname(WEB_DATA_FILE), { recursive: true });
  await writeFile(WEB_DATA_FILE, JSON.stringify(snapshot, null, 2), "utf8");

  console.log(`[done] ${repos.length} repos -> ${WEB_DATA_FILE}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
