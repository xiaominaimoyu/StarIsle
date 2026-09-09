import type {
  ApiErrorBody,
  ApiErrorCode,
  CsrfResponse,
  FileListResponse,
  FileSummary,
  JobResponse,
  JobListResponse,
  JobStatus,
  KnowledgeDocumentListResponse,
  MailDetail,
  MailListResponse,
  PhotoListResponse,
  PhotoUploadResponse,
  ConversationDetail,
  ConversationListResponse,
  ConversationSummary,
  Citation,
  DocumentUploadResponse,
  LoginRequest,
  UserSummary,
  StorageStats,
} from "@star-isle/contracts";
import { apiFetch, API_BASE, ApiError, streamLines } from "./client";

/** 鉴权模块调用 */

export async function fetchCsrf(): Promise<CsrfResponse> {
  return apiFetch<CsrfResponse>("/auth/csrf");
}

export async function login(body: LoginRequest, csrfToken: string): Promise<UserSummary> {
  return apiFetch<UserSummary>("/auth/login", {
    method: "POST",
    body,
    csrfToken,
  });
}

export async function refresh(csrfToken: string): Promise<UserSummary> {
  return apiFetch<UserSummary>("/auth/refresh", { method: "POST", csrfToken });
}

export async function logout(csrfToken: string): Promise<void> {
  return apiFetch<void>("/auth/logout", { method: "POST", csrfToken });
}

export async function fetchMe(): Promise<UserSummary> {
  return apiFetch<UserSummary>("/auth/me");
}

/** 文件模块调用 */

export async function listFiles(cursor?: string): Promise<FileListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<FileListResponse>(`/files${qs}`);
}

export function downloadFileUrl(fileId: string): string {
  return `${API_BASE}/files/${fileId}/download`;
}

export async function deleteFile(fileId: string, csrfToken: string): Promise<void> {
  return apiFetch<void>(`/files/${fileId}`, { method: "DELETE", csrfToken });
}

export async function uploadFile(
  file: File,
  csrfToken: string,
  idempotencyKey: string,
): Promise<FileSummary> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<FileSummary>("/files", {
    method: "POST",
    formData,
    csrfToken,
    idempotencyKey,
  });
}

/** 照片模块调用 */

export async function listPhotos(cursor?: string): Promise<PhotoListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<PhotoListResponse>(`/photos${qs}`);
}

export async function uploadPhoto(
  file: File,
  csrfToken: string,
  idempotencyKey: string,
): Promise<PhotoUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<PhotoUploadResponse>("/photos", {
    method: "POST",
    formData,
    csrfToken,
    idempotencyKey,
  });
}

export async function deletePhoto(photoId: string, csrfToken: string): Promise<void> {
  return apiFetch<void>(`/photos/${photoId}`, { method: "DELETE", csrfToken });
}

/** 邮件模块调用 */

export async function listMail(cursor?: string): Promise<MailListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<MailListResponse>(`/mail${qs}`);
}

export async function fetchMailDetail(messageId: string): Promise<MailDetail> {
  return apiFetch<MailDetail>(`/mail/${messageId}`);
}

export async function markMailRead(
  messageId: string,
  read: boolean,
  csrfToken: string,
): Promise<void> {
  return apiFetch<void>(`/mail/${messageId}/read`, {
    method: "POST",
    body: { read },
    csrfToken,
  });
}

/** 知识库模块调用 */

export async function listDocuments(cursor?: string): Promise<KnowledgeDocumentListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<KnowledgeDocumentListResponse>(`/knowledge/documents${qs}`);
}

export async function uploadDocument(
  file: File,
  csrfToken: string,
  idempotencyKey: string,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<DocumentUploadResponse>("/knowledge/documents", {
    method: "POST",
    formData,
    csrfToken,
    idempotencyKey,
  });
}

export async function deleteDocument(documentId: string, csrfToken: string): Promise<JobResponse> {
  return apiFetch<JobResponse>(`/knowledge/documents/${documentId}`, {
    method: "DELETE",
    csrfToken,
  });
}

export async function rebuildDocument(
  documentId: string,
  reason: string,
  csrfToken: string,
): Promise<JobResponse> {
  return apiFetch<JobResponse>(`/knowledge/documents/${documentId}/rebuild`, {
    method: "POST",
    body: { reason },
    csrfToken,
  });
}

export async function fetchJob(jobId: string): Promise<JobStatus> {
  return apiFetch<JobStatus>(`/jobs/${jobId}`);
}

export async function listJobs(limit = 20, activeOnly = false): Promise<JobListResponse> {
  const params = new URLSearchParams({ limit: String(limit), activeOnly: String(activeOnly) });
  return apiFetch<JobListResponse>(`/jobs?${params.toString()}`);
}

export async function cancelJob(jobId: string, csrfToken: string): Promise<JobStatus> {
  return apiFetch<JobStatus>(`/jobs/${jobId}/cancel`, { method: "POST", csrfToken });
}

export async function fetchStorageStats(): Promise<StorageStats> {
  return apiFetch<StorageStats>("/stats/storage");
}

/** 会话与 RAG 模块调用 */

export async function listConversations(cursor?: string): Promise<ConversationListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<ConversationListResponse>(`/conversations${qs}`);
}

export async function createConversation(
  title: string | undefined,
  csrfToken: string,
): Promise<ConversationSummary> {
  return apiFetch<ConversationSummary>("/conversations", {
    method: "POST",
    body: { title },
    csrfToken,
  });
}

export async function fetchConversation(
  conversationId: string,
  cursor?: string,
): Promise<ConversationDetail> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return apiFetch<ConversationDetail>(`/conversations/${conversationId}${qs}`);
}

export interface RagCallbacks {
  onStage: (stage: "retrieving" | "generating") => void;
  onHeartbeat: (ts: number) => void;
  onToken: (token: string) => void;
  onCitation: (citation: Citation) => void;
  onError: (code: string, message: string) => void;
}

const API_ERROR_CODES = new Set<ApiErrorCode>([
  "BAD_REQUEST",
  "AUTH_REQUIRED",
  "FORBIDDEN",
  "NOT_FOUND",
  "CONFLICT",
  "PAYLOAD_TOO_LARGE",
  "UNSUPPORTED_MEDIA_TYPE",
  "UNPROCESSABLE",
  "RATE_LIMITED",
  "CSRF_REQUIRED",
  "INTERNAL",
  "INDEX_NOT_READY",
]);

function normalizeApiErrorCode(value: unknown): ApiErrorCode {
  return typeof value === "string" && API_ERROR_CODES.has(value as ApiErrorCode)
    ? value as ApiErrorCode
    : "INTERNAL";
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isCitation(value: unknown): value is Citation {
  if (!isRecord(value)) return false;
  return nonEmptyString(value.documentId) !== null
    && nonEmptyString(value.documentName) !== null
    && Number.isInteger(value.version)
    && Number(value.version) > 0
    && nonEmptyString(value.chunkId) !== null
    && typeof value.snippet === "string";
}

export async function ragQuery(
  conversationId: string | undefined,
  query: string,
  csrfToken: string,
  signal: AbortSignal,
  callbacks: RagCallbacks,
): Promise<{ messageId: string; citations: Citation[]; content: string }> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/rag/query`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ conversationId, query }),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("INTERNAL", "网络请求失败,请检查连接后重试。", 0, null);
  }

  const responseRequestId = nonEmptyString(response.headers.get("X-Request-Id"));

  if (!response.ok) {
    let code: ApiErrorCode = "INTERNAL";
    let message = `请求失败(HTTP ${response.status})`;
    let requestId = responseRequestId;
    try {
      const body = await response.json() as Partial<ApiErrorBody>;
      code = normalizeApiErrorCode(body.error?.code);
      message = nonEmptyString(body.error?.message) ?? message;
      requestId = nonEmptyString(body.error?.requestId) ?? requestId;
    } catch {
      // 忽略解析失败
    }
    throw new ApiError(code, message, response.status, requestId);
  }

  const citations: Citation[] = [];
  let content = "";
  let messageId = "";
  let receivedDone = false;
  for await (const line of streamLines(response, signal)) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
    }
    if (!isRecord(parsed) || nonEmptyString(parsed.event) === null || receivedDone) {
      throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
    }
    const evt = parsed;
    switch (evt.event) {
      case "stage":
        if (evt.stage !== "retrieving" && evt.stage !== "generating") {
          throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
        }
        callbacks.onStage(evt.stage);
        break;
      case "heartbeat":
        if (typeof evt.ts !== "number" || !Number.isFinite(evt.ts)) {
          throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
        }
        callbacks.onHeartbeat(evt.ts);
        break;
      case "token":
        {
          if (typeof evt.token !== "string") {
            throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
          }
          const token = evt.token;
          content += token;
          callbacks.onToken(token);
        }
        break;
      case "citation":
        if (!isCitation(evt.citation)) {
          throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
        }
        citations.push(evt.citation);
        callbacks.onCitation(evt.citation);
        break;
      case "error":
        {
          const rawCode = nonEmptyString(evt.code) ?? "INTERNAL";
          const message = nonEmptyString(evt.message) ?? `回答中断(${rawCode})`;
          callbacks.onError(rawCode, message);
          throw new ApiError(
            normalizeApiErrorCode(rawCode),
            message,
            response.status,
            nonEmptyString(evt.requestId) ?? responseRequestId,
          );
        }
      case "done":
        {
          const doneMessageId = nonEmptyString(evt.messageId);
          if (
            doneMessageId === null
            || !Array.isArray(evt.citations)
            || !evt.citations.every(isCitation)
          ) {
            throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
          }
          messageId = doneMessageId;
          citations.splice(0, citations.length, ...evt.citations);
          receivedDone = true;
        }
        break;
      default:
        throw new ApiError("INTERNAL", "回答流格式无效,请重试。", response.status, responseRequestId);
    }
  }
  if (!receivedDone || !messageId) {
    throw new ApiError(
      "INTERNAL",
      "回答流在完成前中断，已生成内容不会保存为完整回答。",
      response.status,
      responseRequestId,
    );
  }
  if (!content.trim()) {
    throw new ApiError(
      "INTERNAL",
      "回答流未返回有效内容，已拒绝将其视为完成回答。",
      response.status,
      responseRequestId,
    );
  }
  return { messageId, citations, content };
}
