/**
 * 星屿 StarIsle 前后端共享契约类型
 *
 * 依据 docs/总体具体开发计划 §7 的 API 契约草案。
 * 前端不维护独立的"猜测版"后端类型,一律从此包导入。
 */

/* ========== 通用 ========== */

export interface ApiErrorBody {
  error: {
    code: ApiErrorCode;
    message: string;
    requestId: string;
  };
}

export type ApiErrorCode =
  | "BAD_REQUEST"
  | "AUTH_REQUIRED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "PAYLOAD_TOO_LARGE"
  | "UNSUPPORTED_MEDIA_TYPE"
  | "UNPROCESSABLE"
  | "RATE_LIMITED"
  | "CSRF_REQUIRED"
  | "INTERNAL"
  | "INDEX_NOT_READY";

export interface CursorPage<T> {
  items: T[];
  nextCursor: string | null;
}

export interface JobStatus {
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number | null;
  errorCode: string | null;
  updatedAt: string;
}

/** 作业列表绝不暴露内部 payload。 */
export interface JobSummary extends JobStatus {
  jobId: string;
  jobType: "ingest" | "rebuild" | "purge";
  documentId: string | null;
}

export interface JobCounts {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  cancelled: number;
}

export interface JobListResponse {
  items: JobSummary[];
  counts: JobCounts;
}

export interface StorageStats {
  usedBytes: number;
  fileCount: number;
  photoCount: number;
}

/* ========== 鉴权 ========== */

export interface CsrfResponse {
  csrfToken: string;
  expiresAt: string;
}

export interface LoginRequest {
  password: string;
}

export interface UserSummary {
  id: string;
  role: "admin";
  sessionExpiresAt: string;
}

/* ========== 仓库快照(构建期) ========== */

export interface Repo {
  source: "github" | "gitee";
  name: string;
  description: string | null;
  url: string;
  language: string | null;
  stars: number;
  forks: number;
  updatedAt: string;
  featured: boolean;
  topics: string[];
}

export interface RepoSnapshot {
  repos: Repo[];
  fetchedAt: string;
  sources: { source: Repo["source"]; ok: boolean; error?: string }[];
}

/* ========== 文件 ========== */

export interface FileSummary {
  id: string;
  name: string;
  size: number;
  mimeType: string;
  createdAt: string;
  tags: string[];
}

export interface FileListResponse extends CursorPage<FileSummary> {}

/* ========== 照片 ========== */

export interface PhotoSummary {
  id: string;
  thumbnailUrl: string;
  captureAt: string | null;
  width: number | null;
  height: number | null;
}

export interface PhotoListResponse extends CursorPage<PhotoSummary> {}

export interface PhotoUploadResponse {
  id: string;
  status: "ready" | "processing";
  thumbnailState: "ready" | "pending";
}

/* ========== 邮件 ========== */

export interface MailSummary {
  id: string;
  from: string;
  subject: string;
  receivedAt: string;
  read: boolean;
}

export interface MailListResponse extends CursorPage<MailSummary> {}

export interface MailAttachment {
  id: string;
  name: string;
  size: number;
  mimeType: string;
}

export interface MailDetail {
  id: string;
  from: string;
  to: string;
  subject: string;
  receivedAt: string;
  read: boolean;
  textBody: string;
  htmlSummary: string | null;
  attachments: MailAttachment[];
}

/* ========== 知识库 ========== */

export type DocumentType = "pdf" | "markdown" | "txt";
export type IndexState = "queued" | "indexing" | "ready" | "failed";

export interface KnowledgeDocument {
  id: string;
  title: string;
  documentType: DocumentType;
  version: number;
  hash: string;
  indexState: IndexState;
  indexErrorCode: string | null;
  createdAt: string;
}

export interface KnowledgeDocumentListResponse extends CursorPage<KnowledgeDocument> {}

export interface DocumentUploadResponse {
  documentId: string;
  jobId: string;
}

export interface JobResponse {
  jobId: string;
}

/* ========== RAG 流式事件 ========== */

export interface Citation {
  documentId: string;
  documentName: string;
  version: number;
  chunkId: string;
  snippet: string;
}

export type RagStreamEvent =
  | { event: "stage"; stage: "retrieving" | "generating" }
  | { event: "heartbeat"; ts: number }
  | { event: "token"; token: string }
  | { event: "citation"; citation: Citation }
  | { event: "error"; code: string; message: string }
  | { event: "done"; messageId: string; citations: Citation[] };

export interface RagQueryRequest {
  conversationId?: string;
  query: string;
}

/* ========== 会话 ========== */

export interface ConversationSummary {
  id: string;
  title: string;
  createdAt: string;
}

export interface ConversationListResponse extends CursorPage<ConversationSummary> {}

export interface MessageSummary {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  createdAt: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  createdAt: string;
  messages: MessageSummary[];
  nextCursor: string | null;
}
