"use client";

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function deriveWsBaseUrlFromApiUrl(apiUrl: string, browserOrigin: string): string {
  const resolved = new URL(apiUrl, browserOrigin);
  const wsProtocol = resolved.protocol === "https:" ? "wss:" : "ws:";
  const normalizedPath = resolved.pathname.replace(/\/api\/proxy\/?$/, "").replace(/\/+$/, "");
  return `${wsProtocol}//${resolved.host}${normalizedPath}`;
}

export function getApiBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return trimTrailingSlash(process.env.NEXT_PUBLIC_API_URL);
  }

  return "/api/proxy";
}

export function getWsBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) {
    return trimTrailingSlash(process.env.NEXT_PUBLIC_WS_URL);
  }

  if (typeof window !== "undefined") {
    return deriveWsBaseUrlFromApiUrl(getApiBaseUrl(), window.location.origin);
  }

  return "ws://localhost:8000";
}
