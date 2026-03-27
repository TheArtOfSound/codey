"use client";

import { useEffect, useRef, useState, useCallback } from "react";

// ── Types ──────────────────────────────────────────────────────────────────

export interface StreamMessage {
  type:
    | "status"
    | "log"
    | "code"
    | "plan"
    | "nfet_before"
    | "nfet_after"
    | "error"
    | "complete";
  data: unknown;
  timestamp: string;
}

export interface CodeChunk {
  file: string;
  language: string;
  content: string;
  action: "create" | "modify" | "delete";
  diff?: string;
}

export interface NfetReport {
  score: number;
  grade: string;
  breakdown: Record<string, number>;
}

export interface SessionPlan {
  steps: Array<{
    id: string;
    description: string;
    status: "pending" | "running" | "done" | "failed";
  }>;
  summary: string;
}

export interface SessionStreamState {
  messages: StreamMessage[];
  status: string;
  connected: boolean;
  codeChunks: CodeChunk[];
  nfetBefore: NfetReport | null;
  nfetAfter: NfetReport | null;
  plan: SessionPlan | null;
  isComplete: boolean;
  error: string | null;
}

// ── Constants ──────────────────────────────────────────────────────────────

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
const MAX_RECONNECT_DELAY = 30_000;
const INITIAL_RECONNECT_DELAY = 1_000;

// ── Hook ───────────────────────────────────────────────────────────────────

export function useSessionStream(sessionId: string | null): SessionStreamState {
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const [status, setStatus] = useState<string>("idle");
  const [connected, setConnected] = useState(false);
  const [codeChunks, setCodeChunks] = useState<CodeChunk[]>([]);
  const [nfetBefore, setNfetBefore] = useState<NfetReport | null>(null);
  const [nfetAfter, setNfetAfter] = useState<NfetReport | null>(null);
  const [plan, setPlan] = useState<SessionPlan | null>(null);
  const [isComplete, setIsComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelayRef = useRef(INITIAL_RECONNECT_DELAY);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const sessionIdRef = useRef(sessionId);

  // Keep sessionId ref current
  sessionIdRef.current = sessionId;

  const resetState = useCallback(() => {
    setMessages([]);
    setStatus("idle");
    setCodeChunks([]);
    setNfetBefore(null);
    setNfetAfter(null);
    setPlan(null);
    setIsComplete(false);
    setError(null);
  }, []);

  const processMessage = useCallback((msg: StreamMessage) => {
    setMessages((prev) => [...prev, msg]);

    switch (msg.type) {
      case "status":
        setStatus(msg.data as string);
        break;

      case "code":
        setCodeChunks((prev) => [...prev, msg.data as CodeChunk]);
        break;

      case "plan": {
        const planData = msg.data as SessionPlan;
        setPlan((prev) => {
          if (!prev) return planData;
          // Merge step statuses for incremental updates
          const merged = { ...prev, ...planData };
          if (planData.steps) {
            merged.steps = planData.steps.map((step) => {
              const existing = prev.steps.find((s) => s.id === step.id);
              return existing ? { ...existing, ...step } : step;
            });
          }
          return merged;
        });
        break;
      }

      case "nfet_before":
        setNfetBefore(msg.data as NfetReport);
        break;

      case "nfet_after":
        setNfetAfter(msg.data as NfetReport);
        break;

      case "error":
        setError(msg.data as string);
        break;

      case "complete":
        setIsComplete(true);
        setStatus("completed");
        break;
    }
  }, []);

  const connect = useCallback(() => {
    if (!sessionIdRef.current || !mountedRef.current) return;

    // Clean up existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    const token =
      typeof window !== "undefined"
        ? localStorage.getItem("codey_token")
        : null;

    const url = `${WS_BASE}/sessions/${sessionIdRef.current}/stream${
      token ? `?token=${encodeURIComponent(token)}` : ""
    }`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      setError(null);
      reconnectDelayRef.current = INITIAL_RECONNECT_DELAY;
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg = JSON.parse(event.data) as StreamMessage;
        processMessage(msg);
      } catch {
        console.error("Failed to parse WebSocket message:", event.data);
      }
    };

    ws.onerror = () => {
      if (!mountedRef.current) return;
      setError("Connection error");
    };

    ws.onclose = (event) => {
      if (!mountedRef.current) return;
      setConnected(false);

      // Don't reconnect if session is complete or component unmounted
      if (isComplete || !sessionIdRef.current) return;

      // Don't reconnect on normal closure (1000) or if server said go away (1008)
      if (event.code === 1000 || event.code === 1008) return;

      // Exponential backoff reconnect
      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(
        delay * 2,
        MAX_RECONNECT_DELAY
      );

      reconnectTimerRef.current = setTimeout(() => {
        if (mountedRef.current && sessionIdRef.current) {
          connect();
        }
      }, delay);
    };
  }, [processMessage, isComplete]);

  // Connect when sessionId changes
  useEffect(() => {
    mountedRef.current = true;

    if (sessionId) {
      resetState();
      connect();
    }

    return () => {
      mountedRef.current = false;

      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [sessionId, connect, resetState]);

  return {
    messages,
    status,
    connected,
    codeChunks,
    nfetBefore,
    nfetAfter,
    plan,
    isComplete,
    error,
  };
}
