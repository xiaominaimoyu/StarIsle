import { useCallback, useEffect, useLayoutEffect, useRef, useState, type SubmitEvent } from "react";
import type { Citation, ConversationSummary, MessageSummary } from "@star-isle/contracts";
import {
  createConversation,
  fetchConversation,
  listConversations,
  ragQuery,
} from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import { MessageList } from "./MessageBubble";

interface StreamState {
  stage: "retrieving" | "generating" | null;
  text: string;
  citations: Citation[];
  error: string | null;
  heartbeatAt: number;
}

interface RetryRequest {
  query: string;
  conversationId: string | null;
}

const MOBILE_DISCLOSURE_QUERY = "(max-width: 1023px)";
const INTERRUPTED_MESSAGE = "回答中断,可重试;已生成的部分不会保存为完整回答。";
const CANCELLED_MESSAGE = "已取消。已生成的部分不会保存为完整回答。";

function emptyStream(): StreamState {
  return { stage: null, text: "", citations: [], error: null, heartbeatAt: 0 };
}

function isMobileDisclosure() {
  return window.matchMedia(MOBILE_DISCLOSURE_QUERY).matches;
}

function mergeUniqueById<T extends { id: string }>(first: T[], second: T[]): T[] {
  const seen = new Set<string>();
  return [...first, ...second].filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

/**
 * 流式问答只在当前 generation 写入页面。会话切换和卸载会先使旧 generation 失效，
 * 防止慢 fetch/SSE 回调污染新会话；只有收到 done 且 ragQuery 验证过非空 token 才固化助手消息。
 */
export default function ChatWindow() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageSummary[]>([]);
  const [input, setInput] = useState("");
  const [stream, setStream] = useState<StreamState>(emptyStream);
  const [conversationListNextCursor, setConversationListNextCursor] = useState<string | null>(null);
  const [conversationListLoading, setConversationListLoading] = useState(false);
  const [conversationListError, setConversationListError] = useState<string | null>(null);
  const [messageNextCursor, setMessageNextCursor] = useState<string | null>(null);
  const [messageLoading, setMessageLoading] = useState(false);
  const [messageError, setMessageError] = useState<string | null>(null);
  const [bottomScrollVersion, setBottomScrollVersion] = useState(0);
  const [showSidebar, setShowSidebar] = useState(false);
  const mountedRef = useRef(false);
  const revisionRef = useRef(0);
  const conversationViewRevisionRef = useRef(0);
  const activeGenerationRef = useRef<number | null>(null);
  const cancelledGenerationRef = useRef<number | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sendInFlightRef = useRef(false);
  const retryRef = useRef<RetryRequest | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const messageViewportRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sidebarToggleRef = useRef<HTMLButtonElement>(null);
  const newConversationRef = useRef<HTMLButtonElement>(null);
  const conversationListRequestRef = useRef(0);
  const conversationListLoadingRef = useRef(false);
  const messageRequestRef = useRef(0);
  const messageLoadingRef = useRef(false);
  const messagePrependAnchorRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);

  const isCurrentRevision = useCallback((revision: number) => (
    mountedRef.current && revisionRef.current === revision
  ), []);

  const isCurrentConversationView = useCallback((revision: number, id: string) => (
    mountedRef.current
    && conversationViewRevisionRef.current === revision
    && conversationIdRef.current === id
  ), []);

  const isWritableGeneration = useCallback((generation: number) => (
    isCurrentRevision(generation) && cancelledGenerationRef.current !== generation
  ), [isCurrentRevision]);

  const updateStreamForGeneration = useCallback((generation: number, updater: (current: StreamState) => StreamState) => {
    if (!isWritableGeneration(generation)) return;
    setStream((current) => (isWritableGeneration(generation) ? updater(current) : current));
  }, [isWritableGeneration]);

  const requestBottomScroll = useCallback(() => {
    setBottomScrollVersion((current) => current + 1);
  }, []);

  const invalidateCurrentWork = useCallback(() => {
    revisionRef.current += 1;
    conversationViewRevisionRef.current += 1;
    activeGenerationRef.current = null;
    cancelledGenerationRef.current = null;
    const controller = abortRef.current;
    abortRef.current = null;
    sendInFlightRef.current = false;
    messageRequestRef.current += 1;
    messageLoadingRef.current = false;
    messagePrependAnchorRef.current = null;
    controller?.abort();
  }, []);

  const loadConversations = useCallback(async () => {
    if (conversationListLoadingRef.current) return;
    const request = conversationListRequestRef.current + 1;
    conversationListRequestRef.current = request;
    conversationListLoadingRef.current = true;
    setConversationListLoading(true);
    try {
      const page = await listConversations();
      if (!mountedRef.current || conversationListRequestRef.current !== request) return;
      // 初始请求若晚于新建会话完成，只补充缺失项，不能覆盖新会话的置顶位置。
      setConversations((current) => mergeUniqueById(current, page.items));
      setConversationListNextCursor(page.nextCursor);
      setConversationListError(null);
    } catch {
      if (mountedRef.current && conversationListRequestRef.current === request) {
        setConversationListError("会话列表加载失败。");
      }
    } finally {
      if (conversationListRequestRef.current === request) {
        conversationListLoadingRef.current = false;
        if (mountedRef.current) setConversationListLoading(false);
      }
    }
  }, []);

  const loadMoreConversations = useCallback(async () => {
    const cursor = conversationListNextCursor;
    if (!cursor || conversationListLoadingRef.current) return;
    const request = conversationListRequestRef.current + 1;
    conversationListRequestRef.current = request;
    conversationListLoadingRef.current = true;
    setConversationListLoading(true);
    setConversationListError(null);
    try {
      const page = await listConversations(cursor);
      if (!mountedRef.current || conversationListRequestRef.current !== request) return;
      setConversations((current) => mergeUniqueById(current, page.items));
      setConversationListNextCursor(page.nextCursor);
    } catch {
      if (mountedRef.current && conversationListRequestRef.current === request) {
        setConversationListError("会话列表加载失败。");
      }
    } finally {
      if (conversationListRequestRef.current === request) {
        conversationListLoadingRef.current = false;
        if (mountedRef.current) setConversationListLoading(false);
      }
    }
  }, [conversationListNextCursor]);

  useEffect(() => {
    mountedRef.current = true;
    void loadConversations();
    return () => {
      // 卸载也遵循与会话切换相同的失效顺序：先 revision，再断开 controller。
      revisionRef.current += 1;
      conversationViewRevisionRef.current += 1;
      activeGenerationRef.current = null;
      cancelledGenerationRef.current = null;
      const controller = abortRef.current;
      abortRef.current = null;
      sendInFlightRef.current = false;
      conversationListRequestRef.current += 1;
      conversationListLoadingRef.current = false;
      messageRequestRef.current += 1;
      messageLoadingRef.current = false;
      messagePrependAnchorRef.current = null;
      controller?.abort();
      mountedRef.current = false;
    };
  }, [loadConversations]);

  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

  useLayoutEffect(() => {
    const anchor = messagePrependAnchorRef.current;
    const viewport = messageViewportRef.current;
    if (!anchor || !viewport) return;
    viewport.scrollTop = anchor.scrollTop + (viewport.scrollHeight - anchor.scrollHeight);
    messagePrependAnchorRef.current = null;
  }, [messages]);

  useEffect(() => {
    if (messagePrependAnchorRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ block: "end" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [bottomScrollVersion, stream.text]);

  useEffect(() => {
    if (!showSidebar || !isMobileDisclosure()) return;
    const frame = window.requestAnimationFrame(() => {
      newConversationRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [showSidebar]);

  useEffect(() => {
    if (!showSidebar) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !isMobileDisclosure()) return;
      event.preventDefault();
      setShowSidebar(false);
      window.requestAnimationFrame(() => {
        sidebarToggleRef.current?.focus();
      });
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [showSidebar]);

  const openConversation = useCallback(async (id: string | null) => {
    // 切换必须先终止旧 generation，旧 stream 的 done/catch/finally 都会失效。
    invalidateCurrentWork();
    const viewRevision = conversationViewRevisionRef.current;
    retryRef.current = null;
    if (!mountedRef.current) return;
    conversationIdRef.current = id;
    setConversationId(id);
    setMessages([]);
    setMessageNextCursor(null);
    setMessageLoading(false);
    setMessageError(null);
    setStream(emptyStream());
    if (!id) return;

    const request = messageRequestRef.current + 1;
    messageRequestRef.current = request;
    messageLoadingRef.current = true;
    setMessageLoading(true);
    try {
      const detail = await fetchConversation(id);
      if (
        !isCurrentConversationView(viewRevision, id)
        || messageRequestRef.current !== request
      ) return;
      if (detail.id !== id) {
        setMessageError("会话加载失败。");
        return;
      }
      // 初始最新页可能与用户在请求期间发送的本地消息交叠；按 ID 保留已有消息，
      // 但历史页始终在当前本地消息之前，不能覆盖或倒置它们。
      setMessages((current) => mergeUniqueById(detail.messages, current));
      setMessageNextCursor(detail.nextCursor);
      setMessageError(null);
      requestBottomScroll();
    } catch {
      if (
        isCurrentConversationView(viewRevision, id)
        && messageRequestRef.current === request
      ) {
        setMessageError("会话加载失败。");
      }
    } finally {
      if (
        isCurrentConversationView(viewRevision, id)
        && messageRequestRef.current === request
      ) {
        messageLoadingRef.current = false;
        setMessageLoading(false);
      }
    }
  }, [invalidateCurrentWork, isCurrentConversationView, requestBottomScroll]);

  const loadOlderMessages = useCallback(async () => {
    const id = conversationIdRef.current;
    const cursor = messageNextCursor;
    if (!id || !cursor || stream.stage !== null || messageLoadingRef.current) return;

    const viewRevision = conversationViewRevisionRef.current;
    const request = messageRequestRef.current + 1;
    messageRequestRef.current = request;
    messageLoadingRef.current = true;
    const viewport = messageViewportRef.current;
    messagePrependAnchorRef.current = viewport
      ? { scrollHeight: viewport.scrollHeight, scrollTop: viewport.scrollTop }
      : null;
    setMessageLoading(true);
    setMessageError(null);
    try {
      const detail = await fetchConversation(id, cursor);
      if (
        !isCurrentConversationView(viewRevision, id)
        || messageRequestRef.current !== request
      ) return;
      if (detail.id !== id) {
        messagePrependAnchorRef.current = null;
        setMessageError("更早消息加载失败。");
        return;
      }
      // API 页内已是 ASC；把更早页前插并按 ID 去重，不假设 user/assistant 成对出现。
      setMessages((current) => mergeUniqueById(detail.messages, current));
      setMessageNextCursor(detail.nextCursor);
    } catch {
      if (
        isCurrentConversationView(viewRevision, id)
        && messageRequestRef.current === request
      ) {
        messagePrependAnchorRef.current = null;
        setMessageError("更早消息加载失败。");
      }
    } finally {
      if (
        isCurrentConversationView(viewRevision, id)
        && messageRequestRef.current === request
      ) {
        messageLoadingRef.current = false;
        setMessageLoading(false);
      }
    }
  }, [isCurrentConversationView, messageNextCursor, stream.stage]);

  const focusQuestionInput = () => {
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
  };

  const selectConversation = (id: string | null) => {
    void openConversation(id);
    if (isMobileDisclosure()) {
      setShowSidebar(false);
      focusQuestionInput();
    }
  };

  const beginQuestion = async (
    suppliedQuery: string,
    suppliedConversationId: string | null,
    retrying: boolean,
  ) => {
    const query = suppliedQuery.trim();
    // 历史页请求先同步置位 ref；同一 JS task 内的 submit/retry 不能在
    // React 状态尚未重新渲染前插入乐观消息并消费滚动锚点。
    if (!query || sendInFlightRef.current || messageLoadingRef.current) return;
    const csrfToken = getCsrf();
    if (!csrfToken) return;

    const generation = revisionRef.current + 1;
    revisionRef.current = generation;
    activeGenerationRef.current = generation;
    cancelledGenerationRef.current = null;
    const previousController = abortRef.current;
    abortRef.current = null;
    previousController?.abort();
    sendInFlightRef.current = true;
    retryRef.current = { query, conversationId: suppliedConversationId };
    if (!retrying) setInput("");
    setStream({ stage: "retrieving", text: "", citations: [], error: null, heartbeatAt: 0 });

    let controller: AbortController | null = null;
    try {
      let currentConversationId = suppliedConversationId;
      if (!currentConversationId) {
        const created = await createConversation(undefined, csrfToken);
        if (!isWritableGeneration(generation)) return;
        currentConversationId = created.id;
        conversationIdRef.current = created.id;
        setConversationId(created.id);
        setConversations((current) => (
          isWritableGeneration(generation) ? mergeUniqueById([created], current) : current
        ));
      }

      if (!isWritableGeneration(generation)) return;
      retryRef.current = { query, conversationId: currentConversationId };
      if (!retrying) {
        setMessages((current) => (
          isWritableGeneration(generation)
            ? [
              ...current,
              {
                id: `local-${Date.now()}`,
                role: "user",
                content: query,
                citations: [],
                createdAt: new Date().toISOString(),
              },
            ]
            : current
        ));
        requestBottomScroll();
      }

      controller = new AbortController();
      abortRef.current = controller;
      const result = await ragQuery(currentConversationId, query, csrfToken, controller.signal, {
        onStage: (stage) => updateStreamForGeneration(generation, (current) => ({ ...current, stage })),
        onHeartbeat: () => updateStreamForGeneration(generation, (current) => ({ ...current, heartbeatAt: 1 })),
        onToken: (token) => updateStreamForGeneration(generation, (current) => ({ ...current, text: current.text + token })),
        onCitation: (citation) => updateStreamForGeneration(generation, (current) => ({
          ...current,
          citations: [...current.citations, citation as Citation],
        })),
        // SSE 事件字段不可信，错误只呈现固定文案。
        onError: () => updateStreamForGeneration(generation, (current) => ({ ...current, error: INTERRUPTED_MESSAGE })),
      });

      if (!isWritableGeneration(generation) || !result.content.trim()) return;
      setMessages((current) => (
        isWritableGeneration(generation)
          ? [
            ...current,
            {
              id: result.messageId,
              role: "assistant",
              content: result.content,
              citations: result.citations,
              createdAt: new Date().toISOString(),
            },
          ]
          : current
      ));
      requestBottomScroll();
      updateStreamForGeneration(generation, () => emptyStream());
    } catch (error) {
      if (!isCurrentRevision(generation)) return;
      const cancelled = cancelledGenerationRef.current === generation
        || (controller?.signal.aborted ?? false)
        || (error instanceof DOMException && error.name === "AbortError");
      if (cancelled) {
        if (cancelledGenerationRef.current !== generation) {
          cancelledGenerationRef.current = generation;
          setStream((current) => (
            isCurrentRevision(generation)
              ? { ...current, stage: null, text: "", citations: [], error: CANCELLED_MESSAGE, heartbeatAt: 0 }
              : current
          ));
        }
      } else {
        updateStreamForGeneration(generation, (current) => ({
          ...current,
          stage: null,
          text: "",
          citations: [],
          error: INTERRUPTED_MESSAGE,
          heartbeatAt: 0,
        }));
      }
    } finally {
      if (!isCurrentRevision(generation) || activeGenerationRef.current !== generation) return;
      if (abortRef.current === controller) abortRef.current = null;
      activeGenerationRef.current = null;
      sendInFlightRef.current = false;
    }
  };

  const sendQuestion = () => {
    void beginQuestion(input, conversationIdRef.current, false);
  };

  const retryQuestion = () => {
    const retry = retryRef.current;
    if (!retry || sendInFlightRef.current) return;
    void beginQuestion(retry.query, retry.conversationId, true);
  };

  const cancelQuestion = () => {
    const generation = activeGenerationRef.current;
    if (!generation || !isCurrentRevision(generation)) return;
    // 用户取消不递增 generation；它仍属于当前视图，只是后续回调不再可写。
    cancelledGenerationRef.current = generation;
    const controller = abortRef.current;
    abortRef.current = null;
    sendInFlightRef.current = false;
    controller?.abort();
    setStream((current) => (
      isCurrentRevision(generation)
        ? { ...current, stage: null, text: "", citations: [], error: CANCELLED_MESSAGE, heartbeatAt: 0 }
        : current
    ));
  };

  const handleSubmit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    sendQuestion();
  };

  const streaming = stream.stage !== null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
      <button
        ref={sidebarToggleRef}
        type="button"
        aria-label={showSidebar ? "关闭会话列表" : "打开会话列表"}
        aria-expanded={showSidebar}
        aria-controls="kb-conversation-list"
        className="inline-flex h-11 w-fit items-center rounded-md border border-border px-4 text-sm text-text-dim hover:border-accent/60 hover:text-text focus-ring lg:hidden"
        onClick={() => setShowSidebar((visible) => !visible)}
      >
        会话列表
      </button>

      <aside
        id="kb-conversation-list"
        aria-label="会话列表"
        className={`${showSidebar ? "flex" : "hidden"} w-full flex-col gap-2 lg:flex lg:w-52 lg:flex-none`}
      >
        <button
          ref={newConversationRef}
          type="button"
          className="inline-flex h-11 items-center rounded-md border border-border px-3 text-xs text-text-dim hover:border-accent/60 focus-ring"
          onClick={() => selectConversation(null)}
        >
          + 新会话
        </button>
        {conversationListError ? <p role="alert" className="text-xs text-danger">{conversationListError}</p> : null}
        <ul className="flex flex-col gap-1 overflow-y-auto">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                aria-pressed={conversationId === conversation.id}
                className={`min-h-11 w-full truncate rounded-md px-3 py-2 text-left text-sm focus-ring ${
                  conversationId === conversation.id ? "bg-accent/15 text-accent" : "text-text-dim hover:bg-surface hover:text-text"
                }`}
                onClick={() => selectConversation(conversation.id)}
              >
                {conversation.title || "新会话"}
              </button>
            </li>
          ))}
        </ul>
        {conversationListNextCursor ? (
          <button
            type="button"
            disabled={conversationListLoading}
            aria-busy={conversationListLoading}
            className="inline-flex h-11 items-center justify-center rounded-md border border-border px-3 text-xs text-text-dim hover:border-accent/60 hover:text-text focus-ring disabled:opacity-50"
            onClick={() => void loadMoreConversations()}
          >
            {conversationListLoading ? "加载中…" : "加载更多会话"}
          </button>
        ) : null}
      </aside>

      <div className="card flex min-h-0 flex-1 flex-col overflow-hidden">
        <div
          ref={messageViewportRef}
          data-testid="kb-chat-history"
          className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4"
        >
          {messageNextCursor ? (
            <button
              type="button"
              disabled={streaming || messageLoading}
              aria-busy={messageLoading}
              className="inline-flex h-11 w-fit items-center rounded-md border border-border px-3 text-xs text-text-dim hover:border-accent/60 hover:text-text focus-ring disabled:opacity-50"
              onClick={() => void loadOlderMessages()}
            >
              {messageLoading ? "加载中…" : "加载更早消息"}
            </button>
          ) : null}
          {messageError ? <p role="alert" className="text-xs text-danger">{messageError}</p> : null}
          {messages.length === 0 && !streaming ? (
            <p className="py-10 text-center text-sm text-text-dim">
              上传文档后,在这里提出关于你知识库的问题。
            </p>
          ) : (
            <MessageList messages={messages} streamingText={streaming ? stream.text : undefined} />
          )}

          {streaming ? (
            <div className="flex items-center gap-2 text-xs text-text-dim" aria-live="polite">
              <span className="heartbeat-dot" aria-hidden="true" />
              <span>
                {stream.stage === "retrieving" ? "检索中…" : "生成中…"}
                {stream.heartbeatAt ? " · 已收到心跳" : ""}
                {stream.citations.length > 0 ? ` · 已引用 ${stream.citations.length} 处` : ""}
              </span>
            </div>
          ) : null}

          {stream.error ? (
            <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-danger">
              <span>{stream.error}</span>
              {retryRef.current ? (
                <button
                  type="button"
                  disabled={messageLoading}
                  className="underline focus-ring disabled:opacity-50"
                  onClick={retryQuestion}
                >
                  重试
                </button>
              ) : null}
            </div>
          ) : null}

          <div ref={bottomRef} />
        </div>

        <form onSubmit={handleSubmit} className="flex items-end gap-2 border-t border-border p-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="输入问题…"
            rows={1}
            aria-label="问题输入框"
            className="min-h-11 flex-1 resize-none rounded-md border border-border bg-transparent px-3 py-2.5 text-sm text-text placeholder:text-text-dim focus:border-accent focus:outline-none"
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendQuestion();
              }
            }}
          />
          {streaming ? (
            <button
              type="button"
              className="inline-flex h-11 items-center rounded-md border border-border px-4 text-sm text-text-dim hover:border-danger/60 hover:text-danger focus-ring"
              onClick={cancelQuestion}
            >
              取消
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || sendInFlightRef.current || messageLoading}
              className="inline-flex h-11 items-center rounded-md bg-accent px-4 text-sm font-medium text-accent-foreground hover:brightness-110 focus-ring disabled:opacity-50"
            >
              发送
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
