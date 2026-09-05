import type { ApiErrorCode, ApiErrorBody } from "@star-isle/contracts";

/**
 * 唯一 fetch 入口:默认同源 /api/v1、credentials: include,
 * 将 API 错误规范化为 ApiError,401/403 交给 session store 处理。
 * 前端绝不存取 JWT;token 只存在于 HttpOnly Cookie 中。
 */

export const API_BASE = import.meta.env.PUBLIC_API_BASE ?? "/api/v1";

export class ApiError extends Error {
  code: ApiErrorCode;
  status: number;
  requestId: string | null;

  constructor(code: ApiErrorCode, message: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
  csrfToken?: string | null;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, formData, csrfToken, idempotencyKey, signal } = options;

  const headers = new Headers();
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (body !== undefined && !formData) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      credentials: "include",
      headers,
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new ApiError("INTERNAL", "网络请求失败,请检查连接后重试。", 0, null);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // 非 JSON 响应
  }

  if (!response.ok) {
    const body = (data as ApiErrorBody | null)?.error;
    throw new ApiError(
      body?.code ?? "INTERNAL",
      body?.message ?? `请求失败(HTTP ${response.status})`,
      response.status,
      body?.requestId ?? null,
    );
  }

  return data as T;
}

/** 读流式响应:POST 流固定使用 fetch + ReadableStream */
export async function* streamLines(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<string> {
  if (!response.body) throw new ApiError("INTERNAL", "响应无数据流", 0, null);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const eventData = (block: string): string | null => {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""));
    return data.length ? data.join("\n") : null;
  };

  const pullEvents = (): string[] => {
    const events: string[] = [];
    // SSE 规范以空行结束事件；先统一 CRLF，支持网络任意分块。
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const data = eventData(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (data !== null) events.push(data);
    }
    return events;
  };

  for (;;) {
    if (signal?.aborted) {
      await reader.cancel();
      throw new DOMException("请求已取消", "AbortError");
    }
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (const data of pullEvents()) yield data;
  }
  buffer += decoder.decode();
  for (const data of pullEvents()) yield data;
  const trailing = eventData(buffer.trim());
  if (trailing !== null) yield trailing;
}
