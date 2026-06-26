"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/ui/ToastProvider";
import { api, type Session, type Repo } from "@/lib/api";
import { repoWorkLanes } from "@/lib/repo-work";
import {
  Code,
  Upload,
  GitBranch,
  Bot,
  Zap,
  Clock,
  ArrowRight,
  Activity,
  ChevronRight,
  ToggleLeft,
  ToggleRight,
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

function healthPhase(score: number | null): {
  label: string;
  color: string;
  bg: string;
} {
  if (score === null) return { label: "N/A", color: "text-codey-text-dim", bg: "bg-codey-card" };
  if (score >= 0.7) return { label: "Healthy", color: "text-codey-green", bg: "bg-codey-green/20" };
  if (score >= 0.4) return { label: "Watch", color: "text-codey-yellow", bg: "bg-codey-yellow/20" };
  return { label: "At Risk", color: "text-codey-red", bg: "bg-codey-red/20" };
}

// ── Quick Action Card ─────────────────────────────────────────────────────────

function QuickAction({
  href,
  icon: Icon,
  title,
  description,
  onClick,
}: {
  href?: string;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  onClick?: () => void;
}) {
  const inner = (
    <div className="group flex cursor-pointer flex-col gap-3 rounded-xl border border-codey-border bg-codey-card p-5 transition-all hover:border-codey-border-light hover:bg-codey-card-hover hover:shadow-glow-green/5">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-codey-green/10">
        <Icon className="h-5 w-5 text-codey-green" />
      </div>
      <div>
        <h3 className="text-sm font-semibold text-codey-text group-hover:text-codey-green transition-colors">
          {title}
        </h3>
        <p className="mt-1 text-xs text-codey-text-dim">{description}</p>
      </div>
      <ArrowRight className="h-4 w-4 text-codey-text-muted transition-transform group-hover:translate-x-1 group-hover:text-codey-green" />
    </div>
  );

  if (onClick) {
    return <button onClick={onClick} className="text-left">{inner}</button>;
  }

  return <Link href={href || "#"} prefetch={false}>{inner}</Link>;
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const { user, refreshUser } = useAuth();
  const router = useRouter();
  const { addToast } = useToast();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);
  const [connectModalOpen, setConnectModalOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [sessData, repoData] = await Promise.all([
        api.getSessions({ limit: 10 }),
        api.getRepos(),
      ]);
      setSessions(sessData.sessions);
      setRepos(repoData);
    } catch (err) {
      const apiErr = err as { detail?: string; status?: number };
      if (apiErr?.status === 401) {
        router.replace("/auth/login");
        return;
      }
      setLoadError(apiErr?.detail || "Could not load your control room. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  // Credit usage derived from real user fields (no faked plan tiers).
  const creditsRemaining = user?.credits_remaining ?? 0;
  const creditCap = Math.max(
    user?.monthly_allocation ?? 0,
    user?.total_credits ?? 0,
    creditsRemaining
  );
  const usedCredits =
    typeof user?.credits_used_this_month === "number"
      ? Math.max(0, user.credits_used_this_month)
      : Math.max(0, creditCap - creditsRemaining);
  // Fill width tracks how much has been USED (not remaining).
  const usagePercent = Math.min(
    100,
    Math.round((usedCredits / Math.max(creditCap, 1)) * 100)
  );

  // Accepts: github.com/owner/repo (with or without https://, optional .git/trailing slash) or bare owner/repo.
  const REPO_INPUT_RE =
    /^(?:https?:\/\/)?(?:www\.)?(?:github\.com\/)?[\w.-]+\/[\w.-]+?(?:\.git)?\/?$/i;

  async function handleConnectRepo() {
    const trimmed = repoUrl.trim();
    if (!trimmed) return;
    if (!REPO_INPUT_RE.test(trimmed)) {
      setConnectError("Enter a repo like github.com/owner/repo (or owner/repo).");
      return;
    }
    setConnecting(true);
    setConnectError(null);
    try {
      const newRepo = await api.connectRepo({ github_url: trimmed });
      setRepos((prev) => [...prev, newRepo]);
      setRepoUrl("");
      setConnectModalOpen(false);
      addToast(`Connected ${newRepo.name}`, "success");
    } catch (err) {
      console.error("Failed to connect repo:", err);
      const apiErr = err as { detail?: string };
      setConnectError(apiErr?.detail || "Failed to connect repository.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnectRepo(repo: Repo) {
    if (!confirm(`Disconnect ${repo.name}? It will be removed from your managed fleet.`)) {
      return;
    }
    setDisconnectingId(repo.id);
    try {
      await api.disconnectRepo(repo.id);
      setRepos((prev) => prev.filter((r) => r.id !== repo.id));
      addToast(`Disconnected ${repo.name}`, "success");
    } catch (err) {
      const apiErr = err as { detail?: string };
      addToast(apiErr?.detail || "Failed to disconnect repository.", "error");
    } finally {
      setDisconnectingId(null);
    }
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-codey-green border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      {loadError && (
        <div className="flex items-center justify-between gap-4 rounded-xl border border-codey-red/40 bg-codey-red/10 px-5 py-3">
          <p className="text-sm text-codey-red">{loadError}</p>
          <button
            onClick={() => load()}
            className="shrink-0 rounded-lg border border-codey-red/50 px-3 py-1.5 text-xs font-semibold text-codey-red transition-colors hover:bg-codey-red/20"
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Welcome + Credits ─────────────────────────────────────────── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">
            Repo control room{user?.name?.trim() ? `, ${user.name.trim()}` : user?.email ? `, ${user.email.split("@")[0]}` : ""}
          </h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            {sessions.length > 0
              ? `${sessions.filter((s) => s.status === "running").length} autonomous run${sessions.filter((s) => s.status === "running").length !== 1 ? "s are" : " is"} active across your queue`
              : "Connect a repository or queue an intervention to start the operating loop"}
          </p>
        </div>

        {/* Credit bar */}
        <div className="w-full max-w-xs rounded-xl border border-codey-border bg-codey-card p-4">
          <div className="flex items-center justify-between text-xs text-codey-text-dim">
            <span className="flex items-center gap-1.5">
              <Zap className="h-3 w-3 text-codey-green" />
              Credits
            </span>
            <Link href="/credits" prefetch={false} className="text-codey-green hover:underline">
              Need more?
            </Link>
          </div>
          <div className="mt-2 flex items-baseline gap-1">
            <span className="text-lg font-bold text-codey-text">
              {creditsRemaining.toLocaleString()}
            </span>
            <span className="text-xs text-codey-text-muted">/ {creditCap.toLocaleString()}</span>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-codey-card-hover">
            <div
              className={`h-full rounded-full transition-all ${
                usagePercent >= 90
                  ? "bg-codey-red"
                  : usagePercent >= 70
                    ? "bg-codey-yellow"
                    : "bg-codey-green"
              }`}
              style={{ width: `${usagePercent}%` }}
            />
          </div>
        </div>
      </div>

      {/* ── Quick Actions ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <QuickAction
          href="/dashboard/prompt"
          icon={Code}
          title="Queue intervention"
          description="Write a maintenance brief against a repository"
        />
        <QuickAction
          href="/dashboard/analyze"
          icon={Upload}
          title="Run repo scan"
          description="Upload a codebase snapshot and surface risk"
        />
        <QuickAction
          icon={GitBranch}
          title="Connect GitHub repo"
          description="Add a repository to the managed fleet"
          onClick={() => setConnectModalOpen(true)}
        />
        <QuickAction
          href="/dashboard/autonomous"
          icon={Bot}
          title="Review autopilot"
          description="Tune policies, queue depth, and execution activity"
        />
      </div>

      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Repo Work Coverage</h2>
          <p className="mt-1 text-xs text-codey-text-dim">
            Codey is scoped around the full repository lifecycle, not just ad hoc prompts.
          </p>
        </div>
        <div className="grid gap-0 md:grid-cols-2 xl:grid-cols-3">
          {repoWorkLanes.map((lane) => (
            <div
              key={lane.id}
              className="border-b border-codey-border/50 px-5 py-4 md:border-r xl:[&:nth-child(3n)]:border-r-0"
            >
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-codey-text">{lane.label}</p>
                <span className="rounded-full border border-codey-border bg-codey-bg px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-codey-text-muted">
                  {lane.modeLabel}
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-codey-text-dim">{lane.summary}</p>
            </div>
          ))}
        </div>
      </div>

      {/* ── Recent Sessions ──────────────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="flex items-center justify-between border-b border-codey-border px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Recent Runs</h2>
          <Link
            href="/dashboard/sessions"
            prefetch={false}
            className="flex items-center gap-1 text-xs text-codey-green hover:underline"
          >
            View all <ChevronRight className="h-3 w-3" />
          </Link>
        </div>

        {sessions.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No runs yet. Queue a repository intervention to start building history.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">Work Item</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="hidden px-5 py-3 font-medium md:table-cell">Credits</th>
                  <th className="hidden px-5 py-3 font-medium lg:table-cell">Health</th>
                  <th className="px-5 py-3 font-medium text-right">When</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => {
                  const phase = healthPhase(session.health_score_after);
                  return (
                    <tr
                      key={session.id}
                      className="border-b border-codey-border/50 transition-colors hover:bg-codey-card-hover"
                    >
                      <td className="max-w-[200px] truncate px-5 py-3 text-codey-text">
                        <Link
                          href={`/dashboard/sessions?id=${session.id}`}
                          prefetch={false}
                          className="hover:text-codey-green transition-colors"
                        >
                          {session.prompt || "Untitled intervention"}
                        </Link>
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
                        {session.credits_used}
                      </td>
                      <td className="hidden px-5 py-3 lg:table-cell">
                        {session.health_score_after !== null ? (
                          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${phase.bg} ${phase.color}`}>
                            {phase.label}
                          </span>
                        ) : (
                          <span className="text-xs text-codey-text-muted">--</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3 text-right text-xs text-codey-text-muted">
                        {relativeTime(session.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Connected Repos ──────────────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="flex items-center justify-between border-b border-codey-border px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Repository Fleet</h2>
          <button
            onClick={() => setConnectModalOpen(true)}
            className="text-xs text-codey-green hover:underline"
          >
            + Connect repo
          </button>
        </div>

        {repos.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No repositories are under management yet. Connect GitHub repos to enable continuous scans and autonomous work.
          </div>
        ) : (
          <div className="divide-y divide-codey-border/50">
            {repos.map((repo) => {
              const phase = healthPhase(repo.health_score);
              return (
                <div
                  key={repo.id}
                  className="flex items-center justify-between px-5 py-4 transition-colors hover:bg-codey-card-hover"
                >
                  <div className="flex items-center gap-3">
                    <GitBranch className="h-4 w-4 text-codey-text-dim" />
                    <div>
                      <p className="text-sm font-medium text-codey-text">{repo.name}</p>
                      <p className="text-xs text-codey-text-muted">{repo.default_branch}</p>
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${phase.bg} ${phase.color}`}
                    >
                      {phase.label}
                    </span>

                    {repo.last_analyzed_at && (
                      <span className="hidden text-xs text-codey-text-muted sm:block">
                        {relativeTime(repo.last_analyzed_at)}
                      </span>
                    )}

                    <div className="flex items-center gap-1">
                      <Link
                        href="/dashboard/analyze"
                        prefetch={false}
                        className="rounded-md border border-codey-border px-2.5 py-1 text-xs text-codey-text-dim transition-colors hover:border-codey-green/50 hover:text-codey-green"
                      >
                        Analyze
                      </Link>
                      <Link
                        href={`/dashboard/prompt?repo=${repo.id}`}
                        prefetch={false}
                        className="rounded-md border border-codey-border px-2.5 py-1 text-xs text-codey-text-dim transition-colors hover:border-codey-green/50 hover:text-codey-green"
                      >
                        Queue run
                      </Link>
                      <button
                        onClick={() => handleDisconnectRepo(repo)}
                        disabled={disconnectingId === repo.id}
                        className="rounded-md border border-codey-border px-2.5 py-1 text-xs text-codey-text-dim transition-colors hover:border-codey-red/50 hover:text-codey-red disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {disconnectingId === repo.id ? "Removing..." : "Disconnect"}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Connect Repo Modal ───────────────────────────────────────── */}
      {connectModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setConnectModalOpen(false)}
          />
          <div className="relative z-10 w-full max-w-md animate-fade-in rounded-2xl border border-codey-border bg-codey-card p-6 shadow-2xl">
            <h3 className="text-lg font-semibold text-codey-text">Connect GitHub Repository</h3>
            <p className="mt-1 text-sm text-codey-text-dim">
              Paste a GitHub repo URL to add it to Codey&apos;s managed fleet for scanning, queueing, and autopilot runs.
            </p>
            {!user?.github_connected && (
              <p className="mt-2 text-xs text-codey-text-muted">
                Private repositories require GitHub access.{" "}
                <Link
                  href="/settings"
                  prefetch={false}
                  className="text-codey-green hover:underline"
                >
                  Connect GitHub in Settings
                </Link>
                .
              </p>
            )}

            <input
              type="text"
              value={repoUrl}
              onChange={(e) => {
                setRepoUrl(e.target.value);
                setConnectError(null);
              }}
              placeholder="https://github.com/user/repo"
              className="mt-4 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-3 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            />
            {connectError && (
              <p className="mt-3 text-sm text-codey-red">{connectError}</p>
            )}

            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => setConnectModalOpen(false)}
                className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover"
              >
                Cancel
              </button>
              <button
                onClick={handleConnectRepo}
                disabled={connecting || !repoUrl.trim()}
                className="rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
              >
                {connecting ? (
                  <span className="flex items-center gap-2">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-codey-bg border-t-transparent" />
                    Connecting...
                  </span>
                ) : (
                  "Connect"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
