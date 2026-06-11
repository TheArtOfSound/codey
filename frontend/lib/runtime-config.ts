"use client";

const CODEY_APP_HOST = "codey.autohustle.online";
const CODEY_API_HOST = "api-codey.autohustle.online";

export function getApiBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL;
  }

  if (typeof window !== "undefined" && window.location.hostname === CODEY_APP_HOST) {
    return `${window.location.protocol}//${CODEY_API_HOST}`;
  }

  return "/api/proxy";
}

export function getWsBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) {
    return process.env.NEXT_PUBLIC_WS_URL;
  }

  if (typeof window !== "undefined") {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";

    if (window.location.hostname === CODEY_APP_HOST) {
      return `${wsProtocol}//${CODEY_API_HOST}`;
    }

    return `${wsProtocol}//${window.location.host}`;
  }

  return "ws://localhost:8000";
}
