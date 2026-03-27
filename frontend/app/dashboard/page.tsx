"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { api, type Session, type Repo } from "@/lib/api";
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

function nfetPhase(score: number | null): {
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

  return <Link href={href || "#"}>{inner}</Link>;
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const { user, refreshUser } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [connectModalOpen, setConnectModalOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const [sessData, repoData] = await Promise.all([
          api.getSessions({ limit: 10 }),
          api.getRepos(),
        ]);
        setSessions(sessData.sessions);
        setRepos(repoData);
      } catch (err) {
        console.error("Failed to load dashboard data:", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const totalCredits = user?.plan === "pro" ? 5000 : user?.plan === "team" ? 20000 : 500;
  const usedCredits = totalCredits - (user?.credits_remaining ?? 0);
  const usagePercent = Math.min(100, Math.round((usedCredits / totalCredits) * 100));

  async function handleConnectRepo() {
    if (!repoUrl.trim()) return;
    setConnecting(true);
    try {
      const newRepo = await api.connectRepo({ github_url: repoUrl.trim() });
      setRepos((prev) => [...prev, newRepo]);
      setRepoUrl("");
      setConnectModalOpen(false);
    } catch (err) {
      console.error("Failed to connect repo:", err);
    } finally {
      setConnecting(false);
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
      {/* ── Welcome + Credits ─────────────────────────────────────────── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">
            Welcome back{user?.email ? `, ${user.email.split("@")[0]}` : ""}
          </h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            {sessions.length > 0
              ? `You have ${sessions.filter((s) => s.status === "running").length} running session${sessions.filter((s) => s.status === "running").length !== 1 ? "s" : ""}`
              : "Start your first session to see it here"}
          </p>
        </div>

        {/* Credit bar */}
        <div className="w-full max-w-xs rounded-xl border border-codey-border bg-codey-card p-4">
          <div className="flex items-center justify-between text-xs text-codey-text-dim">
            <span className="flex items-center gap-1.5">
              <Zap className="h-3 w-3 text-codey-green" />
              Credits
            </span>
            <Link href="/credits" className="text-codey-green hover:underline">
              Need more?
            </Link>
          </div>
          <div className="mt-2 flex items-baseline gap-1">
            <span className="text-lg font-bold text-codey-text">
              {user?.credits_remaining.toLocaleString()}
            </span>
            <span className="text-xs text-codey-text-muted">/ {totalCredits.toLocaleString()}</span>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-codey-card-hover">
            <div
              className={`h-full rounded-full transition-all ${
                usagePercent > 90
                  ? "bg-codey-red"
                  : usagePercent > 70
                    ? "bg-codey-yellow"
                    : "bg-codey-green"
              }`}
              style={{ width: `${100 - usagePercent}%` }}
            />
          </div>
        </div>
      </div>

      {/* ── Quick Actions ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <QuickAction
          href="/dashboard/prompt"
          icon={Code}
          title="Start a new prompt"
          description="Describe what you need, get production code"
        />
        <QuickAction
          href="/dashboard/analyze"
          icon={Upload}
          title="Upload & analyze"
          description="Get structural health analysis for any codebase"
        />
        <QuickAction
          icon={GitBranch}
          title="Connect GitHub repo"
          description="Link a repository for continuous analysis"
          onClick={() => setConnectModalOpen(true)}
        />
        <QuickAction
          href="/dashboard/autonomous"
          icon={Bot}
          title="Autonomous activity"
          description="Monitor and manage auto-fix sessions"
        />
      </div>

      {/* ── Recent Sessions ──────────────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="flex items-center justify-between border-b border-codey-border px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Recent Sessions</h2>
          <Link
            href="/dashboard/sessions"
            className="flex items-center gap-1 text-xs text-codey-green hover:underline"
          >
            View all <ChevronRight className="h-3 w-3" />
          </Link>
        </div>

        {sessions.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No sessions yet. Start your first prompt to see activity here.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">Prompt</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="hidden px-5 py-3 font-medium md:table-cell">Credits</th>
                  <th className="hidden px-5 py-3 font-medium lg:table-cell">Health</th>
                  <th className="px-5 py-3 font-medium text-right">When</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => {
                  const phase = nfetPhase(session.nfet_score_after);
                  return (
                    <tr
                      key={session.id}
                      className="border-b border-codey-border/50 transition-colors hover:bg-codey-card-hover"
                    >
                      <td className="max-w-[200px] truncate px-5 py-3 text-codey-text">
                        <Link
                          href={`/dashboard/sessions?id=${session.id}`}
                          className="hover:text-codey-green transition-colors"
                        >
                          {session.prompt || "Untitled session"}
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
                        {session.nfet_score_after !== null ? (
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
          <h2 className="text-sm font-semibold text-codey-text">Connected Repos</h2>
          <button
            onClick={() => setConnectModalOpen(true)}
            className="text-xs text-codey-green hover:underline"
          >
            + Connect repo
          </button>
        </div>

        {repos.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No repos connected. Link a GitHub repository to enable continuous analysis.
          </div>
        ) : (
          <div className="divide-y divide-codey-border/50">
            {repos.map((repo) => {
              const phase = nfetPhase(repo.nfet_score);
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
              Paste a GitHub repo URL to connect it for analysis and autonomous monitoring.
            </p>

            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/repo"
              className="mt-4 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-3 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            />

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
