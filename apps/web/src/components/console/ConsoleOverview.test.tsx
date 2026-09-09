import { beforeEach, describe, expect, it, vi } from "vitest";

const reactHarness = vi.hoisted(() => ({
  stateIndex: 0,
  stateSetters: [] as Array<ReturnType<typeof vi.fn>>,
  stateOverrides: new Map<number, unknown>(),
}));

const apiMocks = vi.hoisted(() => ({
  fetchStorageStats: vi.fn(),
  listPhotos: vi.fn(),
  listMail: vi.fn(),
  listJobs: vi.fn(),
}));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useCallback: vi.fn((callback: unknown) => callback),
    useEffect: vi.fn(),
    useRef: vi.fn((initial: unknown) => ({ current: initial })),
    useState: vi.fn((initial: unknown) => {
      const index = reactHarness.stateIndex;
      reactHarness.stateIndex += 1;
      const value = reactHarness.stateOverrides.has(index)
        ? reactHarness.stateOverrides.get(index)
        : typeof initial === "function"
          ? (initial as () => unknown)()
          : initial;

      const setter = vi.fn();
      reactHarness.stateSetters.push(setter);
      return [value, setter];
    }),
  };
});

vi.mock("@/lib/api/modules", () => apiMocks);
vi.mock("@/lib/format", () => ({
  formatBytes: vi.fn((bytes: number) => `${bytes}B`),
  formatRelativeTime: vi.fn(() => "刚刚"),
}));

const statusBannerMock = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/StatusBanner", () => ({ default: statusBannerMock }));

import { createElement } from "react";
import ConsoleOverview from "./ConsoleOverview";

interface TestElement {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown };
}

function resetHarness(overrides?: Map<number, unknown>) {
  reactHarness.stateIndex = 0;
  reactHarness.stateSetters = [];
  reactHarness.stateOverrides = overrides ?? new Map();
}

function childElements(node: unknown): TestElement[] {
  const element = node as TestElement;
  const children = element.props?.children;
  return Array.isArray(children) ? children : [children];
}

function findAll(node: unknown, predicate: (el: TestElement) => boolean): TestElement[] {
  const results: TestElement[] = [];
  if (!node || typeof node !== "object") return results;
  const element = node as TestElement;
  if (predicate(element)) results.push(element);
  for (const child of childElements(element)) {
    results.push(...findAll(child, predicate));
  }
  return results;
}

function findFirst(node: unknown, predicate: (el: TestElement) => boolean): TestElement | null {
  return findAll(node, predicate)[0] ?? null;
}

function textOf(node: unknown): string {
  if (node == null || node === false || node === true) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return textOf((node as TestElement).props?.children);
}

function getCards(): TestElement[] {
  resetHarness();
  const overviewTree = ConsoleOverview();
  return childElements(overviewTree).filter((c) => typeof (c as TestElement).type === "function");
}

function expand(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(expand);
  if (!node || typeof node !== "object") return node;
  const el = node as TestElement;
  if (typeof el.type === "function") {
    return expand((el.type as (p: unknown) => unknown)(el.props));
  }
  const children = el.props?.children;
  const newChildren = Array.isArray(children) ? children.map(expand) : expand(children);
  return { ...el, props: { ...el.props, children: newChildren } };
}

function renderCard(cardIndex: number, overrides: Map<number, unknown>): unknown {
  const cards = getCards();
  resetHarness(overrides);
  const cardTree = ((cards[cardIndex] as TestElement).type as () => unknown)();
  return expand(cardTree);
}

const STORAGE = 0, PHOTOS = 1, MAIL = 2, JOBS = 3;

beforeEach(() => {
  vi.clearAllMocks();
  resetHarness();
  statusBannerMock.mockImplementation(({ children, action }: { children: unknown; action: unknown }) =>
    createElement("div", { role: "alert" }, children, action),
  );
  apiMocks.fetchStorageStats.mockResolvedValue({ usedBytes: 0, fileCount: 0, photoCount: 0 });
  apiMocks.listPhotos.mockResolvedValue({ items: [] });
  apiMocks.listMail.mockResolvedValue({ items: [] });
  apiMocks.listJobs.mockResolvedValue({ items: [], counts: { queued: 0, running: 0, failed: 0, cancelled: 0 } });
});

describe("ConsoleOverview structure", () => {
  it("each card shell has a title heading and a full-list link", () => {
    const expectedHrefs = ["/console/files", "/console/gallery", "/console/mail", "/console/kb"];
    const expectedTitles = ["存储用量", "最近照片", "最新邮件", "作业摘要"];
    for (let i = 0; i < 4; i++) {
      const tree = renderCard(i, new Map());
      const links = findAll(tree, (el) => el.type === "a");
      expect(links).toHaveLength(1);
      expect(links[0].props?.href).toBe(expectedHrefs[i]);
      expect(textOf(tree)).toContain(expectedTitles[i]);
    }
  });
});

describe("StorageCard state density", () => {
  it("shows error message and retry button on fetch failure", () => {
    const tree = renderCard(STORAGE, new Map([[1, "存储用量加载失败。"]]));
    expect(textOf(tree)).toContain("存储用量加载失败");
    const buttons = findAll(tree, (el) => el.type === "button");
    expect(buttons.length).toBeGreaterThanOrEqual(1);
    expect(textOf(buttons[0])).toContain("重试");
    expect(buttons[0].props?.onClick).toBeTypeOf("function");
  });

  it("retry click clears error and re-fetches", () => {
    const tree = renderCard(STORAGE, new Map([[1, "存储用量加载失败。"]]));
    const retryButton = findAll(tree, (el) => el.type === "button")[0];
    (retryButton.props?.onClick as () => void)();
    expect(reactHarness.stateSetters[1]).toHaveBeenCalledWith(null);
    expect(apiMocks.fetchStorageStats).toHaveBeenCalled();
  });

  it("renders formatted bytes on success", () => {
    const tree = renderCard(STORAGE, new Map([[0, { usedBytes: 2048, fileCount: 5, photoCount: 3 }]]));
    const text = textOf(tree);
    expect(text).toContain("2048B");
    expect(text).toContain("文件 5 个");
    expect(text).toContain("照片 3 张");
  });
});

describe("PhotosCard state density", () => {
  it("shows empty state when no photos", () => {
    const tree = renderCard(PHOTOS, new Map([[0, []]]));
    expect(textOf(tree)).toContain("还没有照片");
  });

  it("shows error and retry on fetch failure", () => {
    const tree = renderCard(PHOTOS, new Map([[1, "最近照片加载失败。"]]));
    expect(textOf(tree)).toContain("最近照片加载失败");
    const retryButton = findAll(tree, (el) => el.type === "button")[0];
    expect(textOf(retryButton)).toContain("重试");
    expect(retryButton.props?.onClick).toBeTypeOf("function");
  });
});

describe("MailCard state density", () => {
  it("shows empty state when no mail", () => {
    const tree = renderCard(MAIL, new Map([[0, []]]));
    expect(textOf(tree)).toContain("暂无邮件");
  });

  it("shows error and retry on fetch failure", () => {
    const tree = renderCard(MAIL, new Map([[1, "最新邮件加载失败。"]]));
    expect(textOf(tree)).toContain("最新邮件加载失败");
    const retryButton = findAll(tree, (el) => el.type === "button")[0];
    expect(retryButton.props?.onClick).toBeTypeOf("function");
  });
});

describe("JobsCard state density", () => {
  it("shows empty state when no jobs", () => {
    const tree = renderCard(JOBS, new Map([[0, { items: [], counts: { queued: 0, running: 0, failed: 0, cancelled: 0 } }]]));
    expect(textOf(tree)).toContain("暂无知识库作业");
  });

  it("shows error and retry on fetch failure", () => {
    const tree = renderCard(JOBS, new Map([[1, "作业摘要加载失败。"]]));
    expect(textOf(tree)).toContain("作业摘要加载失败");
    const retryButton = findAll(tree, (el) => el.type === "button")[0];
    expect(retryButton.props?.onClick).toBeTypeOf("function");
  });

  it("renders job counts and items on success", () => {
    const tree = renderCard(JOBS, new Map([[0, {
      items: [{ jobId: "j1", jobType: "index", status: "running" }],
      counts: { queued: 1, running: 1, failed: 0, cancelled: 0 },
    }]]));
    const text = textOf(tree);
    expect(text).toContain("排队 1");
    expect(text).toContain("运行 1");
    expect(text).toContain("index · running");
  });
});
