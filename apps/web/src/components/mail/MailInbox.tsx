import { useCallback, useEffect, useRef, useState } from "react";
import type { MailDetail, MailListResponse, MailSummary } from "@star-isle/contracts";
import { fetchMailDetail, listMail, markMailRead } from "@/lib/api/modules";
import { getCsrf } from "@/stores/session";
import { formatRelativeTime } from "@/lib/format";
import EmptyState from "@/components/ui/EmptyState";
import StatusBanner from "@/components/ui/StatusBanner";
import DOMPurify from "dompurify";

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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MailDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [readError, setReadError] = useState<{ id: string; message: string } | null>(null);
  const [loadImages, setLoadImages] = useState(false);
  const [htmlView, setHtmlView] = useState(false);
  const [view, setView] = useState<"list" | "reader">("list");
  const timerRef = useRef<number | null>(null);

  const load = useCallback(async (cursor?: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const res: MailListResponse = await listMail(cursor);
      setMails((prev) => (cursor ? [...prev, ...res.items] : res.items));
      setNextCursor(res.nextCursor);
    } catch {
      setLoadError("邮件列表加载失败,请重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  // 初期 60 秒轮询
  useEffect(() => {
    void load();
    timerRef.current = window.setInterval(() => void load(), 60_000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [load]);

  const markAsRead = useCallback(async (mailId: string) => {
    setReadError(null);
    setMails((prev) => prev.map((m) => (m.id === mailId ? { ...m, read: true } : m)));
    try {
      const token = getCsrf();
      if (!token) throw new Error("missing csrf token");
      await markMailRead(mailId, true, token);
    } catch {
      setMails((prev) => prev.map((m) => (m.id === mailId ? { ...m, read: false } : m)));
      setReadError({ id: mailId, message: "标记已读失败，未读状态已恢复。" });
    }
  }, []);

  const loadDetail = useCallback(async (mail: MailSummary) => {
    setDetailLoading(true);
    setDetailError(null);
    setReadError(null);
    try {
      const d = await fetchMailDetail(mail.id);
      setDetail(d);
      if (!mail.read) {
        // 乐观更新未读状态；失败时由 markAsRead 回滚并暴露明确重试动作。
        await markAsRead(mail.id);
      }
    } catch {
      setDetailError("邮件内容加载失败,请重试。");
    } finally {
      setDetailLoading(false);
    }
  }, [markAsRead]);

  const openMail = (mail: MailSummary) => {
    setSelectedId(mail.id);
    setView("reader");
    setDetail(null);
    setLoadImages(false);
    setHtmlView(false);
    // loadDetail 自己处理所有失败路径，事件处理器不会留下未处理的 Promise。
    void loadDetail(mail);
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
          <button type="button" className="rounded px-2 text-xs text-accent hover:underline focus-ring" onClick={() => load()}>
            刷新
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex flex-col gap-2" aria-busy="true">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="skeleton h-16 rounded-md" />
              ))}
            </div>
          ) : loadError ? (
            <StatusBanner tone="error" action={<button type="button" onClick={() => load()}>重试</button>}>
              {loadError}
            </StatusBanner>
          ) : mails.length === 0 ? (
            <EmptyState title="暂无邮件" description="新邮件到达后会在一分钟内出现在这里。" />
          ) : (
            <ul className="flex flex-col gap-1">
              {mails.map((m) => (
                <li key={m.id}>
                  <button
                    type="button"
                    onClick={() => openMail(m)}
                    data-testid="mail-item"
                    aria-current={selectedId === m.id ? "true" : undefined}
                    className={`w-full rounded-md border px-3 py-2 text-left focus-ring ${
                      selectedId === m.id ? "border-accent/50 bg-accent/10" : "border-transparent hover:bg-surface"
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
                <button type="button" className="mx-auto mt-2 rounded border border-border px-3 py-1.5 text-xs hover:border-accent/60 focus-ring" onClick={() => load(nextCursor)}>
                  加载更多
                </button>
              ) : null}
            </ul>
          )}
        </div>
      </div>

      {/* 阅读器 */}
      <div className={`min-h-0 flex-1 ${view === "list" ? "hidden lg:flex lg:flex-col" : "flex flex-col"}`}>
        {detailLoading ? (
          <div className="skeleton h-full rounded-card" aria-busy="true" />
        ) : detailError ? (
          <StatusBanner tone="error" action={<button type="button" onClick={retryDetail}>重试</button>}>
            {detailError}
          </StatusBanner>
        ) : detail ? (
          <article className="card flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
              <button type="button" className="rounded px-2 text-xs text-accent hover:underline focus-ring lg:hidden" onClick={() => setView("list")}>
                ← 返回列表
              </button>
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
                <div className="flex overflow-hidden rounded-md border border-border text-xs" role="group" aria-label="正文视图">
                  <button
                    type="button"
                    aria-pressed={!htmlView}
                    className={`px-3 py-1 ${!htmlView ? "bg-accent text-accent-foreground" : "text-text-dim"}`}
                    onClick={() => setHtmlView(false)}
                  >
                    纯文本
                  </button>
                  <button
                    type="button"
                    aria-pressed={htmlView}
                    className={`px-3 py-1 ${htmlView ? "bg-accent text-accent-foreground" : "text-text-dim"}`}
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
                  <div className="mb-2 flex items-center gap-2 rounded-md border border-warning/40 bg-surface px-3 py-2 text-xs text-warning" role="status">
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
                  <span key={a.id} className="ml-2 inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5">
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
