import { useState } from "react";
import ChatWindow from "./ChatWindow";
import DocumentManager from "./DocumentManager";

type MobilePanel = "documents" | "chat";

/**
 * 知识库工作区在窄屏一次只展示一个任务面板；lg 及以上同时保留文档和问答区域。
 */
export default function KnowledgeBaseWorkspace() {
  const [activePanel, setActivePanel] = useState<MobilePanel>("documents");

  return (
    <div className="flex min-h-0 flex-col gap-4 lg:h-[calc(100dvh-6rem)]">
      <div
        role="group"
        aria-label="知识库视图"
        className="grid grid-cols-2 rounded-sm border border-border bg-surface p-1 lg:hidden"
      >
        <button
          type="button"
          aria-controls="kb-documents-panel"
          aria-pressed={activePanel === "documents"}
          className={`inline-flex min-h-11 items-center justify-center rounded-sm border-b-2 border-transparent px-3 text-sm font-medium focus-ring ${
            activePanel === "documents"
              ? "border-accent-warm bg-bg text-text"
              : "text-text-dim hover:bg-bg hover:text-text"
          }`}
          onClick={() => setActivePanel("documents")}
        >
          文档
        </button>
        <button
          type="button"
          aria-controls="kb-chat-panel"
          aria-pressed={activePanel === "chat"}
          className={`inline-flex min-h-11 items-center justify-center rounded-sm border-b-2 border-transparent px-3 text-sm font-medium focus-ring ${
            activePanel === "chat"
              ? "border-accent-warm bg-bg text-text"
              : "text-text-dim hover:bg-bg hover:text-text"
          }`}
          onClick={() => setActivePanel("chat")}
        >
          问答
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-6 lg:flex-row">
        <section
          id="kb-documents-panel"
          aria-labelledby="kb-documents-heading"
          className={`${activePanel === "documents" ? "flex" : "hidden"} min-h-0 flex-col gap-3 lg:flex lg:w-2/5 lg:flex-none`}
        >
          <h2 id="kb-documents-heading" className="text-sm font-semibold text-text-dim">
            文档
          </h2>
          <div className="min-h-0 lg:flex-1 lg:overflow-y-auto lg:pr-1">
            <DocumentManager />
          </div>
        </section>

        <section
          id="kb-chat-panel"
          aria-labelledby="kb-chat-heading"
          className={`${activePanel === "chat" ? "flex" : "hidden"} min-h-0 flex-1 flex-col gap-3 lg:flex`}
        >
          <h2 id="kb-chat-heading" className="text-sm font-semibold text-text-dim">
            问答
          </h2>
          <ChatWindow />
        </section>
      </div>
    </div>
  );
}
