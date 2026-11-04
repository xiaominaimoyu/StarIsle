import { useCallback, useEffect, useRef, useState } from "react";
import type { MailDetail, MailListResponse, MailSummary } from "@star-isle/contracts";
import { fetchMailDetail, listMail, markMailRead } from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import { formatRelativeTime } from "@/lib/format";
import EmptyState from "@/components/ui/EmptyState";
import StatusBanner from "@/components/ui/StatusBanner";
import DOMPurify from "dompurify";

type MailListMergeMode = "refresh" | "append";
const EMPTY_MAIL_IDS: ReadonlySet<string> = new Set();

/** 合并独立完成的首屏与游标请求，不丢行且保持稳定顺序。 */
export function mergeMailListPage(
  current: MailSummary[],
  incoming: MailSummary[],
  mode: MailListMergeMode,
  preserveReadIds: ReadonlySet<string> = EMPTY_MAIL_IDS,
): MailSummary[] {
  const currentById = preserveReadIds.size > 0
    ? new Map(current.map((mail) => [mail.id, mail]))
    : null;
  const protectedIncoming = incoming.map((mail) => {
    const currentMail = currentById?.get(mail.id);
    return currentMail && preserveReadIds.has(mail.id)
      ? { ...mail, read: currentMail.read }
      : mail;
  });
  const ordered = mode === "refresh"
    ? [...protectedIncoming, ...current]
    : [...current, ...protectedIncoming];
  const seen = new Set<string>();
  return ordered.filter((mail) => {
    if (seen.has(mail.id)) return false;
    seen.add(mail.id);
    return true;
  });
}

/** 游标页允许跨刷新代际完成；首屏刷新只接受最新一次结果。 */
export function canApplyMailListResponse(
  requestLifecycle: number,
  currentLifecycle: number,
  requestRefreshGeneration: number | null,
  latestRefreshGeneration: number,
): boolean {
  return requestLifecycle === currentLifecycle
    && (requestRefreshGeneration === null
      || requestRefreshGeneration === latestRefreshGeneration);
}

export function canApplyMailDetailResponse(
  requestLifecycle: number,
  currentLifecycle: number,
  requestGeneration: number,
  latestGeneration: number,
): boolean {
  return requestLifecycle === currentLifecycle && requestGeneration === latestGeneration;
}

export function canRollbackMailRead(
  requestLifecycle: number,
  currentLifecycle: number,
  requestGeneration: number,
  latestGeneration: number,
  alreadyConfirmedRead: boolean,
): boolean {
  return requestLifecycle === currentLifecycle
    && requestGeneration === latestGeneration
    && !alreadyConfirmedRead;
}

export function shouldPreserveMailRead(
  mailReadRevision: number,
  requestReadRevision: number,
  mutationInFlight: boolean,
): boolean {
  return mutationInFlight || mailReadRevision > requestReadRevision;
}

/**
 * 收件箱:桌面双栏(列表 + 阅读器),移动单栏互斥切换。
 * 纯文本优先;已清洗 HTML 在受限容器渲染(DOMPurify 浏览器侧补充),远程图片默认阻止。
 * 未读状态乐观更新,API 失败回滚。
 */
export default function MailInbox() {
  const [mails, setMails] = useState<MailSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MailDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [readError, setReadError] = useState<{ id: string; message: string } | null>(null);
  const [loadImages, setLoadImages] = useState(false);
  const [htmlView, setHtmlView] = useState(false);
  const [view, setView] = useState<"list" | "reader">("list");
  const selectedIdRef = useRef<string | null>(null);
  const selectedItemRef = useRef<HTMLButtonElement | null>(null);
  const focusAfterReturnRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const mountedRef = useRef(false);
  const lifecycleRef = useRef(0);
  const refreshGenerationRef = useRef(0);
  const paginationGenerationRef = useRef(0);
  const activeListRequestsRef = useRef(0);
  const paginationInFlightRef = useRef(false);
  const detailGenerationRef = useRef(0);
  const readStateRevisionRef = useRef(0);
  const readStateRevisionByMailRef = useRef(new Map<string, number>());
  const readMutationGenerationRef = useRef(new Map<string, number>());
  const pendingReadMutationRef = useRef(new Map<string, number>());
  const confirmedReadIdsRef = useRef(new Set<string>());

  const advanceReadStateRevision = useCallback((mailId: string) => {
    readStateRevisionRef.current += 1;
    readStateRevisionByMailRef.current.set(mailId, readStateRevisionRef.current);
  }, []);

  const load = useCallback(async (cursor?: string) => {
    if (!mountedRef.current) return;
    const mode: MailListMergeMode = cursor === undefined ? "refresh" : "append";
    if (mode === "append" && paginationInFlightRef.current) return;
    const requestLifecycle = lifecycleRef.current;
    const requestRefreshGeneration = mode === "refresh"
      ? refreshGenerationRef.current + 1
      : null;
    if (requestRefreshGeneration !== null) {
      refreshGenerationRef.current = requestRefreshGeneration;
    }
    const paginationGenerationAtStart = paginationGenerationRef.current;
    const readStateRevisionAtStart = readStateRevisionRef.current;
    const isCurrentRequest = () => mountedRef.current && canApplyMailListResponse(
      requestLifecycle,
      lifecycleRef.current,
      requestRefreshGeneration,
      refreshGenerationRef.current,
    );

    if (mode === "append") {
      paginationInFlightRef.current = true;
      // 从发起追加时就保护游标，失败后的重试也不能被首屏刷新改写。
      paginationGenerationRef.current += 1;
      setLoadingMore(true);
      setLoadMoreError(null);
    } else {
      activeListRequestsRef.current += 1;
      setLoading(true);
      setLoadError(null);
    }
    try {
      const res: MailListResponse = await listMail(cursor);
      if (!isCurrentRequest()) return;
      if (mode === "append") {
        setNextCursor(res.nextCursor);
      } else if (
        paginationGenerationAtStart === 0
        && paginationGenerationRef.current === 0
      ) {
        // 已加载更早页后，后台首屏刷新不能把游标退回并让该页消失。
        setNextCursor(res.nextCursor);
      }
      const preserveReadIds = new Set<string>();
      for (const mail of res.items) {
        const mailReadRevision = readStateRevisionByMailRef.current.get(mail.id) ?? 0;
        const preserveRead = shouldPreserveMailRead(
          mailReadRevision,
          readStateRevisionAtStart,
          pendingReadMutationRef.current.has(mail.id),
        );
        if (mail.read) {
          confirmedReadIdsRef.current.add(mail.id);
        } else if (!preserveRead) {
          confirmedReadIdsRef.current.delete(mail.id);
        }
        if (preserveRead) {
          preserveReadIds.add(mail.id);
        }
      }
      setMails((current) => mergeMailListPage(
        current,
        res.items,
        mode,
        preserveReadIds,
      ));
    } catch {
      if (isCurrentRequest()) {
        if (mode === "append") setLoadMoreError("更多邮件加载失败，请重试。");
        else setLoadError("邮件列表加载失败,请重试。");
      }
    } finally {
      if (mountedRef.current && requestLifecycle === lifecycleRef.current) {
        if (mode === "append") {
          paginationInFlightRef.current = false;
          setLoadingMore(false);
        } else {
          activeListRequestsRef.current = Math.max(0, activeListRequestsRef.current - 1);
          setLoading(activeListRequestsRef.current > 0);
        }
      }
    }
  }, []);

  // 初期 60 秒轮询
  useEffect(() => {
    mountedRef.current = true;
    lifecycleRef.current += 1;
    refreshGenerationRef.current = 0;
    paginationGenerationRef.current = 0;
    activeListRequestsRef.current = 0;
    paginationInFlightRef.current = false;
    detailGenerationRef.current = 0;
    readStateRevisionRef.current = 0;
    readStateRevisionByMailRef.current.clear();
    readMutationGenerationRef.current.clear();
    pendingReadMutationRef.current.clear();
    confirmedReadIdsRef.current.clear();
    void load();
    timerRef.current = window.setInterval(() => void load(), 60_000);
    return () => {
      mountedRef.current = false;
      lifecycleRef.current += 1;
      activeListRequestsRef.current = 0;
      paginationInFlightRef.current = false;
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [load]);

  useEffect(() => {
    if (view !== "list" || !focusAfterReturnRef.current) return;
    focusAfterReturnRef.current = false;
    if (selectedItemRef.current?.isConnected) selectedItemRef.current.focus();
  }, [view]);

  const markAsRead = useCallback(async (mailId: string) => {
    if (!mountedRef.current) return;
    const requestLifecycle = lifecycleRef.current;
    const requestGeneration = (readMutationGenerationRef.current.get(mailId) ?? 0) + 1;
    readMutationGenerationRef.current.set(mailId, requestGeneration);
    pendingReadMutationRef.current.set(mailId, requestGeneration);
    const isCurrentLifecycle = () => mountedRef.current
      && requestLifecycle === lifecycleRef.current;
    setReadError(null);
    advanceReadStateRevision(mailId);
    setMails((prev) => prev.map((m) => (m.id === mailId ? { ...m, read: true } : m)));
    try {
      const token = getCsrf();
      if (!token) throw new Error("missing csrf token");
      await markMailRead(mailId, true, token);
      if (!isCurrentLifecycle()) return;
      confirmedReadIdsRef.current.add(mailId);
      advanceReadStateRevision(mailId);
      setMails((prev) => prev.map((m) => (m.id === mailId ? { ...m, read: true } : m)));
      setReadError((current) => (current?.id === mailId ? null : current));
    } catch {
      if (
        isCurrentLifecycle()
        && canRollbackMailRead(
          requestLifecycle,
          lifecycleRef.current,
          requestGeneration,
          readMutationGenerationRef.current.get(mailId) ?? 0,
          confirmedReadIdsRef.current.has(mailId),
        )
      ) {
        advanceReadStateRevision(mailId);
        setMails((prev) => prev.map((m) => (m.id === mailId ? { ...m, read: false } : m)));
        if (selectedIdRef.current === mailId) {
          setReadError({ id: mailId, message: "标记已读失败，未读状态已恢复。" });
        }
      }
    } finally {
      if (pendingReadMutationRef.current.get(mailId) === requestGeneration) {
        pendingReadMutationRef.current.delete(mailId);
      }
    }
  }, [advanceReadStateRevision]);

  const loadDetail = useCallback(async (mail: MailSummary) => {
    if (!mountedRef.current) return;
    const requestLifecycle = lifecycleRef.current;
    const requestGeneration = detailGenerationRef.current + 1;
    detailGenerationRef.current = requestGeneration;
    const isCurrentRequest = () => mountedRef.current
      && canApplyMailDetailResponse(
        requestLifecycle,
        lifecycleRef.current,
        requestGeneration,
        detailGenerationRef.current,
      );
    setDetailLoading(true);
    setDetailError(null);
    setReadError(null);
    try {
      const d = await fetchMailDetail(mail.id);
      if (!isCurrentRequest()) return;
      setDetail(d);
      if (!mail.read) {
        // 乐观更新未读状态；失败时由 markAsRead 回滚并暴露明确重试动作。
        // 已获取的正文不需要等待独立的已读写入完成。
        void markAsRead(mail.id);
      }
    } catch {
      if (isCurrentRequest()) {
        setDetailError("邮件内容加载失败,请重试。");
      }
    } finally {
      if (isCurrentRequest()) {
        setDetailLoading(false);
      }
    }
  }, [markAsRead]);

  const openMail = (mail: MailSummary, source: HTMLButtonElement) => {
    selectedIdRef.current = mail.id;
    selectedItemRef.current = source;
    setSelectedId(mail.id);
    setView("reader");
    setDetail(null);
    setLoadImages(false);
    setHtmlView(false);
    // loadDetail 自己处理所有失败路径，事件处理器不会留下未处理的 Promise。
    void loadDetail(mail);
  };

  const returnToList = () => {
    // 已离开的阅读器结果不能再渲染正文或发起已读写入。
    detailGenerationRef.current += 1;
    selectedIdRef.current = null;
    focusAfterReturnRef.current = true;
    setView("list");
    setSelectedId(null);
    setDetail(null);
    setDetailLoading(false);
    setDetailError(null);
    setReadError(null);
  };

  const sanitize = (html: string, allowImages: boolean) =>
    DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ["p", "br", "a", "strong", "em", "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "pre", "code", "img", "table", "thead", "tbody", "tr", "th", "td"],
      ALLOWED_ATTR: ["href", "title", "src", "alt"],
      ALLOW_DATA_ATTR: false,
      ALLOWED_URI_REGEXP: /^(?:https?:\/\/|mailto:)/i,
      // 默认与显式加载都不接受 srcset；默认还会移除每个 img src，避免任何
      // 相对、协议相对、同源或远程图片请求。用户点击后从原始服务端摘要重新清洗，
      // 只恢复绝对 http/https src。
      FORBID_ATTR: allowImages ? ["srcset"] : ["src", "srcset"],
    });

  const renderedHtml = (html: string) => {
    // 切换加载图片时必须从服务端已清洗的原始摘要重新执行 DOMPurify，不能从已移除
    // src 的默认结果恢复属性。
    return sanitize(html, loadImages);
  };

  const retryMarkAsRead = () => {
    if (readError) void markAsRead(readError.id);
  };

  const retryDetail = () => {
    const selected = mails.find((mail) => mail.id === selectedId);
    if (selected) void loadDetail(selected);
  };

  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col gap-4 lg:h-[calc(100dvh-6rem)] lg:flex-row">
      {/* 列表 */}
      <div className={`flex min-h-0 flex-1 flex-col ${view === "reader" ? "hidden lg:flex lg:w-72 lg:flex-none" : ""}`}>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-dim">邮件列表</h2>
          <button type="button" disabled={loading} aria-busy={loading} className="rounded px-2 text-xs text-accent hover:underline focus-ring disabled:opacity-60" onClick={() => void load()}>
            {loading && mails.length > 0 ? "刷新中…" : "刷新"}
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loadError ? (
            <div className="mb-2">
              <StatusBanner tone="error" action={<button type="button" onClick={() => void load()}>{mails.length > 0 ? "重试刷新" : "重试"}</button>}>
                {loadError}
              </StatusBanner>
            </div>
          ) : null}
          {loading && mails.length === 0 ? (
            <div className="flex flex-col gap-2" aria-busy="true">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="skeleton h-16 rounded-sm" />
              ))}
            </div>
          ) : mails.length === 0 ? (
            loadError ? null : <EmptyState title="暂无邮件" description="新邮件到达后会在一分钟内出现在这里。" />
          ) : (
            <ul className="flex flex-col gap-1">
              {mails.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    onClick={(event) => openMail(m, event.currentTarget)}
                    data-testid="mail-item"
                    aria-current={selectedId === m.id ? "true" : undefined}
                    className={`w-full rounded-sm border border-l-2 px-3 py-2 text-left focus-ring ${
                      selectedId === m.id ? "border-border border-l-accent-warm bg-surface" : "border-transparent border-l-transparent hover:border-border hover:border-l-border hover:bg-surface"
                    }`}
                  >
                    <span className="flex items-baseline justify-between gap-2">
                      <span className={`truncate text-sm ${m.read ? "font-normal text-text-dim" : "font-semibold text-text"}`}>
                        {!m.read ? <span className="text-accent" aria-label="未读">● </span> : null}
                        {m.subject || "(无主题)"}
                      </span>
                      <span className="shrink-0 text-xs text-text-dim">{formatRelativeTime(m.receivedAt)}</span>
                    </span>
                    <span className="block truncate text-xs text-text-dim">{m.from}</span>
                  </button>
                </li>
              ))}
              {nextCursor ? (
                <li className="flex flex-col items-center gap-2">
                  {loadMoreError ? <p role="status" className="text-sm text-danger">{loadMoreError}</p> : null}
                  <button type="button" disabled={loadingMore} aria-busy={loadingMore} className="mx-auto mt-2 min-h-11 rounded-sm border border-border bg-surface/70 px-3 py-1.5 text-xs hover:border-accent-warm/70 focus-ring disabled:opacity-60" onClick={() => void load(nextCursor)}>
                    {loadingMore ? "加载中…" : loadMoreError ? "重试加载更多" : "加载更多"}
                  </button>
                </li>
              ) : null}
            </ul>
          )}
        </div>
      </div>

      {/* 阅读器 */}
      <div className={`min-h-0 flex-1 ${view === "list" ? "hidden lg:flex lg:flex-col" : "flex flex-col"}`}>
        <button type="button" className="mb-2 inline-flex min-h-11 shrink-0 items-center self-start rounded-sm px-2 text-sm text-accent hover:underline focus-ring lg:hidden" onClick={returnToList}>
          ← 返回列表
        </button>
        {detailLoading ? (
          <div className="skeleton h-full rounded-sm" aria-busy="true" />
        ) : detailError ? (
          <StatusBanner tone="error" action={<button type="button" onClick={retryDetail}>重试</button>}>
            {detailError}
          </StatusBanner>
        ) : detail ? (
          <article className="card flex min-h-0 flex-1 flex-col overflow-hidden rounded-sm border-t-2 border-t-accent-warm/60">
            <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
              <h2 className="truncate text-base font-semibold text-text">{detail.subject || "(无主题)"}</h2>
            </div>
            <div className="border-b border-border/50 px-4 py-2 text-xs text-text-dim">
              <span>发件人:{detail.from}</span> · <span>收件人:{detail.to}</span> ·{" "}
              <span>{formatRelativeTime(detail.receivedAt)}</span>
            </div>
            {readError?.id === detail.id ? (
              <div className="px-4 pt-3">
                <StatusBanner tone="error" action={<button type="button" onClick={retryMarkAsRead}>重试标记已读</button>}>
                  {readError.message}
                </StatusBanner>
              </div>
            ) : null}

            {detail.htmlSummary ? (
              <div className="flex gap-2 border-b border-border/50 px-4 py-2">
                <div className="flex overflow-hidden rounded-sm border border-border bg-surface/60 text-xs" role="group" aria-label="正文视图">
                  <button
                    type="button"
                    aria-pressed={!htmlView}
                    className={`border-b-2 px-3 py-1 ${!htmlView ? "border-accent-warm bg-bg text-text" : "border-transparent text-text-dim"}`}
                    onClick={() => setHtmlView(false)}
                  >
                    纯文本
                  </button>
                  <button
                    type="button"
                    aria-pressed={htmlView}
                    className={`border-b-2 px-3 py-1 ${htmlView ? "border-accent-warm bg-bg text-text" : "border-transparent text-text-dim"}`}
                    onClick={() => setHtmlView(true)}
                  >
                    HTML 视图
                  </button>
                </div>
                {htmlView && !loadImages ? (
                  <button
                    type="button"
                    className="rounded px-2 text-xs text-accent hover:underline focus-ring"
                    onClick={() => setLoadImages(true)}
                  >
                    本次加载图片
                  </button>
                ) : null}
              </div>
            ) : null}

            {!htmlView || !detail.htmlSummary ? (
              <div className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap px-4 py-4 text-sm leading-relaxed text-text">
                {detail.textBody || "(该邮件没有纯文本正文)"}
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto border-t border-border px-4 py-4">
                {!loadImages ? (
                  <div className="mb-2 flex items-center gap-2 rounded-sm border border-warning/40 border-l-2 border-l-warning bg-surface px-3 py-2 text-xs text-warning" role="status">
                    <span aria-hidden="true">⚠</span>
                    已阻止该邮件的远程图片。信任发件人时可选择本次加载。
                  </div>
                ) : null}
                <div
                  data-testid="mail-html-content"
                  className="[&_a]:text-accent [&_a]:underline [&_img]:max-w-full"
                  dangerouslySetInnerHTML={{
                    __html: renderedHtml(detail.htmlSummary),
                  }}
                />
              </div>
            )}

            {detail.attachments.length > 0 ? (
              <div className="border-t border-border px-4 py-3 text-xs text-text-dim">
                附件:
                {detail.attachments.map((a) => (
                  <span key={a.id} className="ml-2 inline-flex items-center gap-1 rounded-sm border border-border px-2 py-0.5 font-mono">
                    {a.name}
                  </span>
                ))}
              </div>
            ) : null}
          </article>
        ) : (
          <div className="hidden flex-1 items-center justify-center text-sm text-text-dim lg:flex">
            选择一封邮件开始阅读
          </div>
        )}
      </div>
    </div>
  );
}
