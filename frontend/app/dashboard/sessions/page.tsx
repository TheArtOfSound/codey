"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type Session } from "@/lib/api";
import {
  Clock,
  Activity,
  ChevronDown,
  ChevronUp,
  Filter,
  Search,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Code,
  Upload,
  Bot,
  Zap,
} from "lucide-react";

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusColor(status: Session["status"]): string {
  switch (status) {
    case "running":
    case "queued":
      return "bg-codey-yellow/20 text-codey-yellow";
    case "completed":
      return "bg-codey-green/20 text-codey-green";
    case "failed":
    case "cancelled":
      return "bg-codey-red/20 text-codey-red";
    default:
      return "bg-codey-card text-codey-text-dim";
  }
}

function modeFromPrompt(prompt: string): string {
  if (prompt.startsWith("[autonomous]")) return "autonomous";
  if (prompt.startsWith("[analyze]")) return "analyze";
  return "prompt";
}

function modeIcon(mode: string) {
  switch (mode) {
    case "autonomous":
      return Bot;
    case "analyze":
      return Upload;
    default:
      return Code;
  }
}

function nfetPhase(score: number | null): { label: string; color: string; bg: string } {
  if (score === null) return { label: "N/A", color: "text-codey-text-dim", bg: "bg-codey-card" };
  if (score >= 0.7) return { label: "Healthy", color: "text-codey-green", bg: "bg-codey-green/20" };
  if (score >= 0.4) return { label: "Watch", color: "text-codey-yellow", bg: "bg-codey-yellow/20" };
  return { label: "At Risk", color: "text-codey-red", bg: "bg-codey-red/20" };
}

// ── Types ─────────────────────────────────────────────────────────────────────

type ModeFilter = "all" | "prompt" | "analyze" | "autonomous";
type StatusFilter = "all" | "running" | "completed" | "failed";

const PAGE_SIZE = 15;

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [modeFilter, setModeFilter] = useState<ModeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getSessions({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        status: statusFilter !== "all" ? statusFilter : undefined,
      });
      setSessions(data.sessions);
      setTotal(data.total);
    } catch (err) {
      console.error("Failed to load sessions:", err);
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Client-side mode filter (mode isn't a server filter in our API)
  const filteredSessions =
    modeFilter === "all"
      ? sessions
      : sessions.filter((s) => modeFromPrompt(s.prompt) === modeFilter);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-codey-text">Session History</h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          All your Codey sessions in one place. Click a row to expand.
        </p>
      </div>

      {/* ── Filters ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <Filter className="h-4 w-4 text-codey-text-muted" />

        {/* Mode filter */}
        <div className="flex rounded-lg border border-codey-border">
          {(["all", "prompt", "analyze", "autonomous"] as ModeFilter[]).map(
            (mode) => (
              <button
                key={mode}
                onClick={() => {
                  setModeFilter(mode);
                  setPage(0);
                }}
                className={`px-3 py-1.5 text-xs font-medium capitalize transition-colors first:rounded-l-lg last:rounded-r-lg ${
                  modeFilter === mode
                    ? "bg-codey-green/10 text-codey-green"
                    : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                }`}
              >
                {mode}
              </button>
            )
          )}
        </div>

        {/* Status filter */}
        <div className="flex rounded-lg border border-codey-border">
          {(["all", "running", "completed", "failed"] as StatusFilter[]).map(
            (status) => (
              <button
                key={status}
                onClick={() => {
                  setStatusFilter(status);
                  setPage(0);
                }}
                className={`px-3 py-1.5 text-xs font-medium capitalize transition-colors first:rounded-l-lg last:rounded-r-lg ${
                  statusFilter === status
                    ? "bg-codey-green/10 text-codey-green"
                    : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                }`}
              >
                {status}
              </button>
            )
          )}
        </div>
      </div>

      {/* ── Sessions Table ─────────────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-codey-green" />
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className="px-5 py-16 text-center text-sm text-codey-text-dim">
            No sessions found matching your filters.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium">Mode</th>
                  <th className="px-5 py-3 font-medium">Prompt</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="hidden px-5 py-3 font-medium md:table-cell">Credits</th>
                  <th className="hidden px-5 py-3 font-medium lg:table-cell">Health</th>
                  <th className="px-5 py-3 w-8" />
                </tr>
              </thead>
              <tbody>
                {filteredSessions.map((session) => {
                  const isExpanded = expandedId === session.id;
                  const mode = modeFromPrompt(session.prompt);
                  const ModeIcon = modeIcon(mode);
                  const phase = nfetPhase(session.nfet_score_after);

                  return (
                    <>
                      <tr
                        key={session.id}
                        onClick={() =>
                          setExpandedId(isExpanded ? null : session.id)
                        }
                        className="cursor-pointer border-b border-codey-border/50 transition-colors hover:bg-codey-card-hover"
                      >
                        <td className="whitespace-nowrap px-5 py-3 text-xs text-codey-text-dim">
                          {formatDate(session.created_at)}
                        </td>
                        <td className="px-5 py-3">
                          <span className="flex items-center gap-1.5 text-xs capitalize text-codey-text-dim">
                            <ModeIcon className="h-3 w-3" />
                            {mode}
                          </span>
                        </td>
                        <td className="max-w-[250px] truncate px-5 py-3 text-codey-text">
                          {session.prompt.replace(/^\[(.*?)\]\s*/, "").slice(0, 80)}
                        </td>
                        <td className="px-5 py-3">
                          <span
                            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${statusColor(session.status)}`}
                          >
                            {session.status === "running" && (
                              <Activity className="mr-1 h-3 w-3 animate-pulse" />
                            )}
                            {session.status}
                          </span>
                        </td>
                        <td className="hidden px-5 py-3 text-codey-text-dim md:table-cell">
                          <span className="flex items-center gap-1">
                            <Zap className="h-3 w-3 text-codey-text-muted" />
                            {session.credits_used}
                          </span>
                        </td>
                        <td className="hidden px-5 py-3 lg:table-cell">
                          {session.nfet_score_after !== null ? (
                            <span
                              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${phase.bg} ${phase.color}`}
                            >
                              {phase.label}
                            </span>
                          ) : (
                            <span className="text-xs text-codey-text-muted">
                              --
                            </span>
                          )}
                        </td>
                        <td className="px-5 py-3">
                          {isExpanded ? (
                            <ChevronUp className="h-4 w-4 text-codey-text-muted" />
                          ) : (
                            <ChevronDown className="h-4 w-4 text-codey-text-muted" />
                          )}
                        </td>
                      </tr>

                      {/* Expanded row */}
                      {isExpanded && (
                        <tr key={`${session.id}-expanded`}>
                          <td
                            colSpan={7}
                            className="border-b border-codey-border bg-codey-bg/50 px-5 py-5"
                          >
                            <div className="space-y-4">
                              {/* Full prompt */}
                              <div>
                                <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                                  Full Prompt
                                </p>
                                <p className="mt-1 whitespace-pre-wrap rounded-lg bg-codey-card p-3 text-sm text-codey-text">
                                  {session.prompt}
                                </p>
                              </div>

                              {/* Output summary */}
                              {session.result_summary && (
                                <div>
                                  <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                                    Output Summary
                                  </p>
                                  <p className="mt-1 rounded-lg bg-codey-card p-3 text-sm text-codey-text-dim">
                                    {session.result_summary}
                                  </p>
                                </div>
                              )}

                              {/* Health Impact */}
                              <div className="flex flex-wrap gap-4">
                                {session.nfet_score_before !== null && (
                                  <div className="rounded-lg bg-codey-card px-4 py-3">
                                    <p className="text-xs text-codey-text-muted">
                                      Health Before
                                    </p>
                                    <p className="mt-1 text-lg font-bold text-codey-text">
                                      {session.nfet_score_before.toFixed(3)}
                                    </p>
                                  </div>
                                )}
                                {session.nfet_score_after !== null && (
                                  <div className="rounded-lg bg-codey-card px-4 py-3">
                                    <p className="text-xs text-codey-text-muted">
                                      Health After
                                    </p>
                                    <p
                                      className={`mt-1 text-lg font-bold ${
                                        session.nfet_score_after >
                                        (session.nfet_score_before ?? 0)
                                          ? "text-codey-green"
                                          : "text-codey-red"
                                      }`}
                                    >
                                      {session.nfet_score_after.toFixed(3)}
                                    </p>
                                  </div>
                                )}
                                <div className="rounded-lg bg-codey-card px-4 py-3">
                                  <p className="text-xs text-codey-text-muted">
                                    Credits Used
                                  </p>
                                  <p className="mt-1 text-lg font-bold text-codey-text">
                                    {session.credits_used}
                                  </p>
                                </div>
                                {session.completed_at && (
                                  <div className="rounded-lg bg-codey-card px-4 py-3">
                                    <p className="text-xs text-codey-text-muted">
                                      Duration
                                    </p>
                                    <p className="mt-1 text-lg font-bold text-codey-text">
                                      {Math.round(
                                        (new Date(session.completed_at).getTime() -
                                          new Date(session.created_at).getTime()) /
                                          1000
                                      )}
                                      s
                                    </p>
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* ── Pagination ───────────────────────────────────────────── */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-codey-border px-5 py-3">
            <p className="text-xs text-codey-text-muted">
              Showing {page * PAGE_SIZE + 1}–
              {Math.min((page + 1) * PAGE_SIZE, total)} of {total}
            </p>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded-lg p-1.5 text-codey-text-dim hover:bg-codey-card-hover disabled:opacity-30"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const pageNum =
                  totalPages <= 5
                    ? i
                    : Math.max(
                        0,
                        Math.min(page - 2, totalPages - 5)
                      ) + i;
                return (
                  <button
                    key={pageNum}
                    onClick={() => setPage(pageNum)}
                    className={`h-8 w-8 rounded-lg text-xs font-medium transition-colors ${
                      page === pageNum
                        ? "bg-codey-green/10 text-codey-green"
                        : "text-codey-text-dim hover:bg-codey-card-hover"
                    }`}
                  >
                    {pageNum + 1}
                  </button>
                );
              })}
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="rounded-lg p-1.5 text-codey-text-dim hover:bg-codey-card-hover disabled:opacity-30"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
