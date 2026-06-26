"use client";

export type OAuthProvider = "github" | "google";
export type OAuthIntent = "login" | "signup" | "connect";

export interface PendingOAuthRequest {
  provider: OAuthProvider;
  redirectTo: string;
  state: string;
  intent?: OAuthIntent;
  referrerId?: string;
}

const OAUTH_STORAGE_KEY = "codey_oauth_request";

export function storePendingOAuthRequest(request: PendingOAuthRequest): void {
  if (typeof window === "undefined") {
    return;
  }

  sessionStorage.setItem(OAUTH_STORAGE_KEY, JSON.stringify(request));
}

export function loadPendingOAuthRequest(): PendingOAuthRequest | null {
  if (typeof window === "undefined") {
    return null;
  }

  const raw = sessionStorage.getItem(OAUTH_STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw) as PendingOAuthRequest;
  } catch {
    sessionStorage.removeItem(OAUTH_STORAGE_KEY);
    return null;
  }
}

export function clearPendingOAuthRequest(): void {
  if (typeof window === "undefined") {
    return;
  }

  sessionStorage.removeItem(OAUTH_STORAGE_KEY);
}
