"use client";

import { Fragment, useEffect, useState, useCallback } from "react";
import Link from "next/link";
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
  AlertTriangle,
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

function modeFromSession(session: Session): string {
  if (session.mode) return session.mode;
  const prompt = session.prompt || "";
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

function modeLabel(mode: string): string {
  switch (mode) {
    case "prompt":
      return "queued run";
    case "analyze":
      return "repo scan";
    case "autonomous":
      return "autopilot";
    default:
      return mode;
  }
}

function healthPhase(score: number | null): { label: string; color: string; bg: string } {
  if (score === null) return { label: "N/A", color: "text-codey-text-dim", bg: "bg-codey-card" };
  if (score >= 0.7) return { label: "Healthy", color: "text-codey-green", bg: "bg-codey-green/20" };
  if (score >= 0.4) return { label: "Watch", color: "text-codey-yellow", bg: "bg-codey-yellow/20" };
  return { label: "At Risk", color: "text-codey-red", bg: "bg-codey-red/20" };
}

// ── Types ─────────────────────────────────────────────────────────────────────

type ModeFilter = "all" | "prompt" | "analyze" | "autonomous";
type StatusFilter = "all" | "running" | "completed" | "failed";

const PAGE_SIZE = 15;

const RUN_STATUS_META: Record<string, { label: string; cls: string }> = {
  completed_with_patch: { label: "patch applied", cls: "bg-codey-green/20 text-codey-green" },
  completed_no_changes: { label: "no changes", cls: "bg-codey-yellow/20 text-codey-yellow" },
  completed_read_only: { label: "proposed only", cls: "bg-codey-yellow/20 text-codey-yellow" },
  failed_patch_not_applied: { label: "patch not applied", cls: "bg-codey-red/20 text-codey-red" },
  failed_verification: { label: "claims failed", cls: "bg-codey-red/20 text-codey-red" },
  failed_tests: { label: "tests failed", cls: "bg-codey-red/20 text-codey-red" },
  failed_runtime: { label: "runtime error", cls: "bg-codey-red/20 text-codey-red" },
};

function triState(v: boolean | null | undefined): { label: string; cls: string } {
  if (v === true) return { label: "passed", cls: "text-codey-green" };
  if (v === false) return { label: "failed", cls: "text-codey-red" };
  return { label: "not run", cls: "text-codey-text-muted" };
}

// Patch-receipt panel: warnings + claim verification + diff + validation.
function ReceiptPanel({ session }: { session: Session }) {
  const r = session.patch_receipt;
  const filesChanged = r?.filesChanged ?? [];
  const noFiles = (session.files_modified ?? 0) === 0 || filesChanged.length === 0;
  const claimFailed = session.verification_passed === false;
  const v = r?.validation;
  const noValidation = v
    ? !(v.syntaxChecked || v.testsPassed || v.buildPassed || v.typecheckPassed || v.lintPassed)
    : true;
  const mismatches = (r?.claimsMade ?? []).filter(
    (c) => c.checkable !== false && !c.matchedByDiff
  );

  return (
    <div className="space-y-3">
      {claimFailed && (
        <div className="rounded-lg border border-codey-red/40 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
          <p className="font-semibold">Claim verification failed.</p>
          <p>The explanation describes edits that are not present in the actual patch.</p>
        </div>
      )}
      {noFiles && (
        <div className="rounded-lg border border-codey-yellow/40 bg-codey-yellow/10 px-4 py-3 text-sm text-codey-yellow">
          <p className="font-semibold">No repository files were changed.</p>
          <p>This output is a generated suggestion only.</p>
        </div>
      )}
      {noValidation && (
        <div className="rounded-lg border border-codey-border bg-codey-card px-4 py-2 text-xs text-codey-text-dim">
          No validation commands were run.
        </div>
      )}

      {r && (
        <div className="flex flex-wrap gap-2 text-xs">
          {(() => {
            const meta = RUN_STATUS_META[r.status] ?? {
              label: r.status,
              cls: "bg-codey-card text-codey-text-dim",
            };
            return (
              <span className={`rounded-full px-2.5 py-0.5 font-medium ${meta.cls}`}>
                {meta.label}
              </span>
            );
          })()}
          <span className="rounded-full bg-codey-card px-2.5 py-0.5 text-codey-text-dim">
            intent: {r.intent}
          </span>
          <span className="rounded-full bg-codey-card px-2.5 py-0.5 text-codey-text-dim">
            files: {filesChanged.length}
          </span>
          <span className="rounded-full bg-codey-card px-2.5 py-0.5">
            <span className="text-codey-text-muted">claims: </span>
            <span className={triState(session.verification_passed).cls}>
              {triState(session.verification_passed).label}
            </span>
          </span>
          {v && (
            <>
              <span className="rounded-full bg-codey-card px-2.5 py-0.5">
                <span className="text-codey-text-muted">tests: </span>
                <span className={triState(v.testsPassed).cls}>{triState(v.testsPassed).label}</span>
              </span>
              <span className="rounded-full bg-codey-card px-2.5 py-0.5">
                <span className="text-codey-text-muted">build: </span>
                <span className={triState(v.buildPassed).cls}>{triState(v.buildPassed).label}</span>
              </span>
            </>
          )}
          {session.health_score !== null && (
            <span className="rounded-full bg-codey-card px-2.5 py-0.5 text-codey-text-dim">
              run health: {session.health_score.toFixed(2)}
            </span>
          )}
        </div>
      )}

      {mismatches.length > 0 && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
            Claim verification
          </p>
          <ul className="mt-1 space-y-1">
            {(r?.claimsMade ?? []).map((c, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className={c.matchedByDiff || c.checkable === false ? "text-codey-green" : "text-codey-red"}>
                  {c.matchedByDiff || c.checkable === false ? "✓" : "✗"}
                </span>
                <span className="text-codey-text-dim">
                  {c.claim}
                  {c.mismatchReason && (
                    <span className="block text-xs text-codey-red">{c.mismatchReason}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {filesChanged.length > 0 && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
            Files changed
          </p>
          <ul className="mt-1 space-y-0.5 text-sm text-codey-text">
            {filesChanged.map((f, i) => (
              <li key={i} className="font-mono text-xs">
                <span className="text-codey-text-dim">{f.changeKind}</span> {f.path}{" "}
                <span className="text-codey-green">+{f.additions}</span>{" "}
                <span className="text-codey-red">-{f.deletions}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {r?.diffText && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
            Diff (hash {r.diffHash.slice(0, 12)})
          </p>
          <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-codey-bg p-3 text-xs font-mono text-codey-text-dim">
            {r.diffText}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [modeFilter, setModeFilter] = useState<ModeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
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
      setError("Failed to load session history.");
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
      : sessions.filter((s) => modeFromSession(s) === modeFilter);
  const visibleSessions = filteredSessions.filter((session) => {
    const haystack = `${session.prompt} ${session.result_summary || ""}`.toLowerCase();
    return haystack.includes(query.toLowerCase());
  });

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-codey-text">Run History</h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          All your Codey repo runs in one place. Click a row to expand.
        </p>
      </div>

      {/* ── Filters ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <Filter className="h-4 w-4 text-codey-text-muted" />

        <div className="relative min-w-[220px] flex-1 sm:flex-none">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-codey-text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search run briefs and results"
            className="w-full rounded-lg border border-codey-border bg-codey-card px-9 py-2 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30 sm:w-64"
          />
        </div>

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
                {mode === "all" ? "all" : modeLabel(mode)}
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
        {error && (
          <div className="border-b border-codey-border px-5 py-4 text-sm text-codey-red">
            {error}
          </div>
        )}
        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-codey-green" />
          </div>
        ) : visibleSessions.length === 0 ? (
          <div className="px-5 py-16 text-center text-sm text-codey-text-dim">
            No runs found matching your filters.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium">Mode</th>
                  <th className="px-5 py-3 font-medium">Brief</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="hidden px-5 py-3 font-medium md:table-cell">Credits</th>
                  <th className="hidden px-5 py-3 font-medium lg:table-cell">Health</th>
                  <th className="px-5 py-3 w-8" />
                </tr>
              </thead>
              <tbody>
                {visibleSessions.map((session) => {
                  const isExpanded = expandedId === session.id;
                  const mode = modeFromSession(session);
                  const ModeIcon = modeIcon(mode);
                  const phase = healthPhase(session.health_score_after);

                  return (
                    <Fragment key={session.id}>
                      <tr
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
                            {modeLabel(mode)}
                          </span>
                        </td>
                        <td className="max-w-[250px] truncate px-5 py-3 text-codey-text">
                          {session.prompt.replace(/^\[(.*?)\]\s*/, "").slice(0, 80)}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex flex-col gap-1">
                            <span
                              className={`inline-flex w-fit items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${statusColor(session.status)}`}
                            >
                              {session.status === "running" && (
                                <Activity className="mr-1 h-3 w-3 animate-pulse" />
                              )}
                              {session.status}
                            </span>
                            {session.run_status && RUN_STATUS_META[session.run_status] && (
                              <span
                                className={`inline-flex w-fit items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${RUN_STATUS_META[session.run_status].cls}`}
                              >
                                {RUN_STATUS_META[session.run_status].label}
                              </span>
                            )}
                            {session.verification_passed === false && (
                              <span className="inline-flex w-fit items-center gap-1 text-[10px] font-medium text-codey-red">
                                <AlertTriangle className="h-3 w-3" /> claims unverified
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="hidden px-5 py-3 text-codey-text-dim md:table-cell">
                          <span className="flex items-center gap-1">
                            <Zap className="h-3 w-3 text-codey-text-muted" />
                            {session.credits_used}
                          </span>
                        </td>
                        <td className="hidden px-5 py-3 lg:table-cell">
                          {session.health_score_after !== null ? (
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
                                  Full Brief
                                </p>
                                <p className="mt-1 whitespace-pre-wrap rounded-lg bg-codey-card p-3 text-sm text-codey-text">
                                  {session.prompt}
                                </p>
                              </div>

                              {/* Patch receipt: proof of what actually changed */}
                              <ReceiptPanel session={session} />

                              {/* Output summary */}
                              {session.result_summary && (
                                <div>
                                  <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                                    Stored Output
                                  </p>
                                  <p className="mt-1 rounded-lg bg-codey-card p-3 text-sm text-codey-text-dim">
                                    {session.result_summary}
                                  </p>
                                </div>
                              )}

                              {session.error_message && (
                                <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
                                  <div className="flex items-start gap-2">
                                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                                    <span>{session.error_message}</span>
                                  </div>
                                </div>
                              )}

                              {/* Health Impact */}
                              <div className="flex flex-wrap gap-4">
                                {session.health_score_before !== null && (
                                  <div className="rounded-lg bg-codey-card px-4 py-3">
                                    <p className="text-xs text-codey-text-muted">
                                      Health Before
                                    </p>
                                    <p className="mt-1 text-lg font-bold text-codey-text">
                                      {session.health_score_before.toFixed(3)}
                                    </p>
                                  </div>
                                )}
                                {session.health_score_after !== null && (
                                  <div className="rounded-lg bg-codey-card px-4 py-3">
                                    <p className="text-xs text-codey-text-muted">
                                      Health After
                                    </p>
                                    <p
                                      className={`mt-1 text-lg font-bold ${
                                        session.health_score_after >
                                        (session.health_score_before ?? 0)
                                          ? "text-codey-green"
                                          : "text-codey-red"
                                      }`}
                                    >
                                      {session.health_score_after.toFixed(3)}
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
                                <div className="rounded-lg bg-codey-card px-4 py-3">
                                  <p className="text-xs text-codey-text-muted">
                                    Lines Generated
                                  </p>
                                  <p className="mt-1 text-lg font-bold text-codey-text">
                                    {session.lines_generated}
                                  </p>
                                </div>
                                <div className="rounded-lg bg-codey-card px-4 py-3">
                                  <p className="text-xs text-codey-text-muted">
                                    Files Modified
                                  </p>
                                  <p className="mt-1 text-lg font-bold text-codey-text">
                                    {session.files_modified}
                                  </p>
                                </div>
                              </div>

                              <div className="flex flex-wrap gap-3">
                                {mode === "prompt" && (
                                  <>
                                    <Link
                                      href={`/dashboard/prompt?session=${encodeURIComponent(session.id)}`}
                                      className="rounded-lg border border-codey-green/30 bg-codey-green/10 px-4 py-2 text-sm font-medium text-codey-green transition-colors hover:bg-codey-green/20"
                                    >
                                      Open workspace
                                    </Link>
                                    <Link
                                      href={`/dashboard/prompt?repo=${encodeURIComponent(session.repo_id || "")}`}
                                      className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
                                    >
                                      New run with same repo
                                    </Link>
                                  </>
                                )}
                                {mode === "analyze" && (
                                  <Link
                                    href="/dashboard/analyze"
                                    className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
                                  >
                                    Reopen analyze
                                  </Link>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
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
