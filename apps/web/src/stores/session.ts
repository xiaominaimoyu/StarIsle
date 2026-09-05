import { atom } from "nanostores";
import type { UserSummary } from "@star-isle/contracts";

/**
 * Session 摘要(不存取 JWT):
 * - status: idle | checking | authenticated | anonymous
 * - csrfToken: 会话绑定 token,供 unsafe 请求携带 X-CSRF-Token
 */

export type SessionStatus =
  | "idle"
  | "checking"
  | "authenticated"
  | "anonymous";

export const sessionStatus = atom<SessionStatus>("idle");
export const sessionUser = atom<UserSummary | null>(null);
export const csrfToken = atom<string | null>(null);
export const csrfExpiresAt = atom<number | null>(null);

export function setAuthenticated(user: UserSummary): void {
  sessionUser.set(user);
  sessionStatus.set("authenticated");
}

export function setAnonymous(): void {
  sessionUser.set(null);
  sessionStatus.set("anonymous");
}

export function setChecking(): void {
  sessionStatus.set("checking");
}

export function setCsrf(token: string | null, expiresAt: number | null): void {
  csrfToken.set(token);
  csrfExpiresAt.set(expiresAt);
}

export function isAuthenticated(): boolean {
  return sessionStatus.get() === "authenticated";
}

export function getCsrf(): string | null {
  const token = csrfToken.get();
  const expires = csrfExpiresAt.get();
  if (!token) return null;
  if (expires && expires < Date.now()) {
    csrfToken.set(null);
    return null;
  }
  return token;
}