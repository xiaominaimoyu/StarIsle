import { beforeEach, describe, expect, it, vi } from "vitest";

const reactHarness = vi.hoisted(() => ({
  stateIndex: 0,
  stateSetters: [] as Array<ReturnType<typeof vi.fn>>,
  stateOverrides: new Map<number, unknown>(),
}));

const apiMocks = vi.hoisted(() => ({
  createConversation: vi.fn(),
  fetchConversation: vi.fn(),
  listConversations: vi.fn(),
  ragQuery: vi.fn(),
}));

const sessionMocks = vi.hoisted(() => ({ getCsrf: vi.fn() }));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useCallback: vi.fn((callback: unknown) => callback),
    useEffect: vi.fn(),
    useLayoutEffect: vi.fn(),
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
vi.mock("@/stores/session", () => sessionMocks);
vi.mock("./MessageBubble", () => ({ MessageList: vi.fn(() => null) }));

import ChatWindow from "./ChatWindow";

interface TestElement {
  type?: unknown;
  props?: Record<string, unknown> & { children?: unknown };
}

function findHostElement(node: unknown, type: string): TestElement | null {
  if (!node || typeof node !== "object") return null;
  const element = node as TestElement;
  if (element.type === type) return element;
  const children = element.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const match = findHostElement(child, type);
    if (match) return match;
  }
  return null;
}

beforeEach(() => {
  vi.clearAllMocks();
  reactHarness.stateIndex = 0;
  reactHarness.stateSetters = [];
  reactHarness.stateOverrides = new Map([[3, "知识库问题"]]);
  sessionMocks.getCsrf.mockReturnValue(null);
});

describe("ChatWindow submission guards", () => {
  it("reports a missing CSRF token instead of silently dropping the question", () => {
    const tree = ChatWindow();
    const form = findHostElement(tree, "form");
    const preventDefault = vi.fn();

    expect(form?.props?.onSubmit).toBeTypeOf("function");
    (form?.props?.onSubmit as (event: { preventDefault: () => void }) => void)({ preventDefault });

    expect(preventDefault).toHaveBeenCalledOnce();
    expect(reactHarness.stateSetters[4]).toHaveBeenCalledWith({
      stage: null,
      text: "",
      citations: [],
      error: "会话安全校验信息缺失,请刷新页面后重试。",
      heartbeatAt: 0,
    });
    expect(apiMocks.createConversation).not.toHaveBeenCalled();
    expect(apiMocks.ragQuery).not.toHaveBeenCalled();
  });
});
