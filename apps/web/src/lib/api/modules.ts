import type {
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
import { apiFetch, API_BASE, streamLines } from "./client";

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

export async function ragQuery(
  conversationId: string | undefined,
  query: string,
  csrfToken: string,
  signal: AbortSignal,
  callbacks: RagCallbacks,
): Promise<{ messageId: string; citations: Citation[]; content: string }> {
  const response = await fetch(`${API_BASE}/rag/query`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify({ conversationId, query }),
    signal,
  });

  if (!response.ok) {
    let message = `请求失败(HTTP ${response.status})`;
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
    } catch {
      // 忽略解析失败
    }
    throw new Error(message);
  }

  const citations: Citation[] = [];
  let content = "";
  let messageId = "";
  let receivedDone = false;
  for await (const line of streamLines(response, signal)) {
    let evt: Record<string, unknown>;
    try {
      evt = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }
    switch (evt.event) {
      case "stage":
        callbacks.onStage(evt.stage as "retrieving" | "generating");
        break;
      case "heartbeat":
        callbacks.onHeartbeat(Number(evt.ts));
        break;
      case "token":
        {
          const token = String(evt.token);
          content += token;
          callbacks.onToken(token);
        }
        break;
      case "citation":
        if (evt.citation && typeof evt.citation === "object") {
          const citation = evt.citation as Citation;
          citations.push(citation);
          callbacks.onCitation(citation);
        }
        break;
      case "error":
        {
          const code = String(evt.code);
          const message = String(evt.message);
          callbacks.onError(code, message);
          throw new Error(message || `回答中断(${code})`);
        }
      case "done":
        messageId = String(evt.messageId);
        if (Array.isArray(evt.citations)) {
          citations.splice(0, citations.length, ...(evt.citations as Citation[]));
        }
        receivedDone = true;
        break;
    }
  }
  if (!receivedDone || !messageId) {
    throw new Error("回答流在完成前中断，已生成内容不会保存为完整回答。");
  }
  if (!content.trim()) {
    throw new Error("回答流未返回有效内容，已拒绝将其视为完成回答。");
  }
  return { messageId, citations, content };
}
