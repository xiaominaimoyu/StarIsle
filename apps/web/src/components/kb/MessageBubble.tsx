import type { Citation, MessageSummary } from "@star-isle/contracts";
import { formatRelativeTime } from "@/lib/format";

/** 同一消息内稳定；即使后端重复返回 chunk 也不会产生 React key 冲突。 */
export function citationRenderKey(citation: Citation, index: number): string {
  return `${citation.documentId}:${citation.version}:${citation.chunkId}:${index}`;
}

/** 用户右对齐(accent 淡底);助手左对齐(surface);流式期间末尾渲染闪烁光标 */
export function MessageBubble({
  role,
  content,
  streaming = false,
}: {
  role: MessageSummary["role"];
  content: string;
  streaming?: boolean;
}) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl bg-accent/20 px-4 py-2.5 text-sm leading-relaxed text-text">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-2xl border border-border bg-surface px-4 py-2.5 text-sm leading-relaxed text-text">
        {content}
        {streaming ? <span className="stream-cursor" aria-hidden="true" /> : null}
      </div>
    </div>
  );
}

/** 引用卡:[序号] 文档名 vX · chunk #n + 片段摘要;无引用回答下方显示 warning Banner */
export function CitationCard({ citation, index }: { citation: Citation; index: number }) {
  return (
    <details className="group rounded-md border border-border bg-surface/60 px-3 py-2">
      <summary className="cursor-pointer list-none text-xs text-text-dim marker:hidden">
        <span className="mr-1 inline-flex size-4 items-center justify-center rounded-full bg-accent/20 text-[0.7em] font-semibold text-accent">
          {index + 1}
        </span>
        {citation.documentName} v{citation.version} · chunk {citation.chunkId}
      </summary>
      <p className="mt-2 text-xs leading-relaxed text-text-dim">{citation.snippet}</p>
    </details>
  );
}

/** 消息流 + 引用卡列表 */
export function MessageList({
  messages,
  streamingText,
}: {
  messages: MessageSummary[];
  streamingText?: string | null;
}) {
  const hasStreaming = streamingText !== undefined && streamingText !== null;
  return (
    <div className="flex flex-col gap-3">
      {messages.map((m) => (
        <div key={m.id} className="flex flex-col gap-1.5">
          <MessageBubble role={m.role} content={m.content} />
          {m.citations.length > 0 ? (
            <div className="flex max-w-[85%] flex-col gap-1.5">
              {m.citations.map((c, i) => (
                <CitationCard key={citationRenderKey(c, i)} citation={c} index={i} />
              ))}
            </div>
          ) : null}
          {m.role === "assistant" && m.citations.length === 0 ? (
            <div
              role="status"
              className="flex max-w-[85%] items-center gap-2 rounded-md border border-accent-warm/40 bg-surface px-3 py-2 text-xs text-accent-warm"
            >
              <span aria-hidden="true">⚠</span>
              <span>本次回答未找到可引用的来源</span>
            </div>
          ) : null}
          <span className="text-xs text-text-dim">{formatRelativeTime(m.createdAt)}</span>
        </div>
      ))}
      {hasStreaming ? (
        <div className="flex flex-col gap-1.5">
          <MessageBubble role="assistant" content={streamingText ?? ""} streaming />
        </div>
      ) : null}
    </div>
  );
}
