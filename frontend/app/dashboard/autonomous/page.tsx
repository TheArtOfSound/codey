"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type Repo } from "@/lib/api";
import {
  Bot,
  PauseCircle,
  PlayCircle,
  Settings,
  Zap,
  RotateCcw,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Loader2,
  GitBranch,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface RepoConfig {
  stressThreshold: number;
  maxImpactRadius: number;
  allowedActions: {
    refactor: boolean;
    fixBugs: boolean;
    optimizePerf: boolean;
    updateDeps: boolean;
  };
  autonomous: boolean;
}

interface ActivityEntry {
  id: string;
  repoId: string;
  repoName: string;
  timestamp: string;
  trigger: string;
  component: string;
  stressBefore: number;
  stressAfter: number;
  rolledBack: boolean;
  creditsUsed: number;
  description: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

// ── Toggle Switch ─────────────────────────────────────────────────────────────

function Toggle({
  enabled,
  onChange,
  disabled,
}: {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={() => !disabled && onChange(!enabled)}
      disabled={disabled}
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 ${
        enabled ? "bg-codey-green" : "bg-codey-border"
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition-transform ${
          enabled ? "translate-x-5" : "translate-x-0"
        }`}
      />
    </button>
  );
}

// ── Slider ────────────────────────────────────────────────────────────────────

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  displayValue,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  displayValue?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="text-xs text-codey-text-dim">{label}</label>
        <span className="text-xs font-medium text-codey-text">
          {displayValue ?? value}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full accent-codey-green"
      />
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AutonomousPage() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [configs, setConfigs] = useState<Record<string, RepoConfig>>({});
  const [expandedRepo, setExpandedRepo] = useState<string | null>(null);
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [allPaused, setAllPaused] = useState(false);
  const [creditsUsedThisMonth, setCreditsUsedThisMonth] = useState(0);

  useEffect(() => {
    async function load() {
      try {
        const repoData = await api.getRepos();
        setRepos(repoData);

        // Initialize configs with defaults
        const defaultConfigs: Record<string, RepoConfig> = {};
        repoData.forEach((repo) => {
          defaultConfigs[repo.id] = {
            stressThreshold: 0.6,
            maxImpactRadius: 15,
            allowedActions: {
              refactor: true,
              fixBugs: true,
              optimizePerf: false,
              updateDeps: false,
            },
            autonomous: false,
          };
        });
        setConfigs(defaultConfigs);

        // Mock activity data
        setActivities([
          {
            id: "1",
            repoId: repoData[0]?.id || "",
            repoName: repoData[0]?.name || "example-repo",
            timestamp: new Date(Date.now() - 3600000).toISOString(),
            trigger: "Stress threshold exceeded (0.82)",
            component: "AuthService",
            stressBefore: 0.82,
            stressAfter: 0.54,
            rolledBack: false,
            creditsUsed: 5,
            description: "Extracted shared authentication logic into middleware",
          },
          {
            id: "2",
            repoId: repoData[0]?.id || "",
            repoName: repoData[0]?.name || "example-repo",
            timestamp: new Date(Date.now() - 7200000).toISOString(),
            trigger: "Stress threshold exceeded (0.76)",
            component: "PaymentProcessor",
            stressBefore: 0.76,
            stressAfter: 0.78,
            rolledBack: true,
            creditsUsed: 3,
            description: "Attempted to split processPayment into smaller functions — rolled back due to regression",
          },
          {
            id: "3",
            repoId: repoData[0]?.id || "",
            repoName: repoData[0]?.name || "example-repo",
            timestamp: new Date(Date.now() - 86400000).toISOString(),
            trigger: "Scheduled scan",
            component: "DatabaseAdapter",
            stressBefore: 0.71,
            stressAfter: 0.48,
            rolledBack: false,
            creditsUsed: 4,
            description: "Added connection pooling configuration",
          },
        ]);
        setCreditsUsedThisMonth(47);
      } catch (err) {
        console.error("Failed to load autonomous data:", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  function updateConfig(repoId: string, update: Partial<RepoConfig>) {
    setConfigs((prev) => ({
      ...prev,
      [repoId]: { ...prev[repoId], ...update },
    }));
  }

  function toggleAction(repoId: string, action: keyof RepoConfig["allowedActions"]) {
    setConfigs((prev) => ({
      ...prev,
      [repoId]: {
        ...prev[repoId],
        allowedActions: {
          ...prev[repoId].allowedActions,
          [action]: !prev[repoId].allowedActions[action],
        },
      },
    }));
  }

  function handlePauseAll() {
    setAllPaused(true);
    setConfigs((prev) => {
      const updated = { ...prev };
      Object.keys(updated).forEach((key) => {
        updated[key] = { ...updated[key], autonomous: false };
      });
      return updated;
    });
  }

  function handleResumeAll() {
    setAllPaused(false);
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">Autonomous Mode</h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Configure auto-fix agents that monitor your repos and reduce structural stress.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 rounded-lg border border-codey-border bg-codey-card px-3 py-2 text-xs text-codey-text-dim">
            <Zap className="h-3 w-3 text-codey-green" />
            {creditsUsedThisMonth} credits used this month
          </div>
          <button
            onClick={allPaused ? handleResumeAll : handlePauseAll}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              allPaused
                ? "border border-codey-green/30 bg-codey-green/10 text-codey-green hover:bg-codey-green/20"
                : "border border-codey-red/30 bg-codey-red-glow text-codey-red hover:bg-codey-red/20"
            }`}
          >
            {allPaused ? (
              <>
                <PlayCircle className="h-4 w-4" />
                Resume all
              </>
            ) : (
              <>
                <PauseCircle className="h-4 w-4" />
                Pause all
              </>
            )}
          </button>
        </div>
      </div>

      {/* ── Repo List with Toggles ─────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Connected Repositories</h2>
        </div>

        {repos.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No repos connected. Connect a GitHub repository from the dashboard to enable autonomous mode.
          </div>
        ) : (
          <div className="divide-y divide-codey-border/50">
            {repos.map((repo) => {
              const config = configs[repo.id];
              if (!config) return null;
              const isExpanded = expandedRepo === repo.id;

              return (
                <div key={repo.id}>
                  {/* Repo row */}
                  <div className="flex items-center justify-between px-5 py-4">
                    <button
                      onClick={() =>
                        setExpandedRepo(isExpanded ? null : repo.id)
                      }
                      className="flex items-center gap-3 text-left"
                    >
                      {isExpanded ? (
                        <ChevronDown className="h-4 w-4 text-codey-text-muted" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-codey-text-muted" />
                      )}
                      <GitBranch className="h-4 w-4 text-codey-text-dim" />
                      <div>
                        <p className="text-sm font-medium text-codey-text">
                          {repo.name}
                        </p>
                        <p className="text-xs text-codey-text-muted">
                          {repo.default_branch}
                        </p>
                      </div>
                    </button>
                    <div className="flex items-center gap-3">
                      <span
                        className={`text-xs font-medium ${
                          config.autonomous
                            ? "text-codey-green"
                            : "text-codey-text-muted"
                        }`}
                      >
                        {config.autonomous ? "Active" : "Off"}
                      </span>
                      <Toggle
                        enabled={config.autonomous}
                        onChange={(v) => updateConfig(repo.id, { autonomous: v })}
                        disabled={allPaused}
                      />
                    </div>
                  </div>

                  {/* Config panel */}
                  {isExpanded && (
                    <div className="border-t border-codey-border/50 bg-codey-bg/50 px-5 py-5">
                      <div className="grid gap-6 sm:grid-cols-2">
                        {/* Sliders */}
                        <div className="space-y-5">
                          <Slider
                            label="Stress threshold"
                            value={config.stressThreshold}
                            min={0.3}
                            max={0.9}
                            step={0.05}
                            onChange={(v) =>
                              updateConfig(repo.id, { stressThreshold: v })
                            }
                            displayValue={config.stressThreshold.toFixed(2)}
                          />
                          <Slider
                            label="Max impact radius"
                            value={config.maxImpactRadius}
                            min={5}
                            max={50}
                            step={5}
                            onChange={(v) =>
                              updateConfig(repo.id, { maxImpactRadius: v })
                            }
                            displayValue={`${config.maxImpactRadius} components`}
                          />
                        </div>

                        {/* Allowed actions */}
                        <div>
                          <p className="text-xs font-medium text-codey-text-dim mb-3">
                            Allowed Actions
                          </p>
                          <div className="space-y-3">
                            {(
                              [
                                { key: "refactor" as const, label: "Refactor code" },
                                { key: "fixBugs" as const, label: "Fix bugs" },
                                { key: "optimizePerf" as const, label: "Optimize performance" },
                                { key: "updateDeps" as const, label: "Update dependencies" },
                              ] as const
                            ).map((action) => (
                              <label
                                key={action.key}
                                className="flex items-center gap-3 cursor-pointer"
                              >
                                <input
                                  type="checkbox"
                                  checked={config.allowedActions[action.key]}
                                  onChange={() =>
                                    toggleAction(repo.id, action.key)
                                  }
                                  className="h-4 w-4 rounded border-codey-border bg-codey-bg text-codey-green accent-codey-green focus:ring-codey-green/30"
                                />
                                <span className="text-sm text-codey-text-dim">
                                  {action.label}
                                </span>
                              </label>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Activity Log ───────────────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Activity Log</h2>
        </div>

        {activities.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No autonomous activity yet. Enable autonomous mode on a repo to get started.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">When</th>
                  <th className="px-5 py-3 font-medium">Trigger</th>
                  <th className="px-5 py-3 font-medium">Component</th>
                  <th className="px-5 py-3 font-medium">Before</th>
                  <th className="px-5 py-3 font-medium">After</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium text-right">Credits</th>
                </tr>
              </thead>
              <tbody>
                {activities.map((entry) => (
                  <tr
                    key={entry.id}
                    className="border-b border-codey-border/50 hover:bg-codey-card-hover"
                  >
                    <td className="whitespace-nowrap px-5 py-3 text-xs text-codey-text-muted">
                      <Clock className="mr-1 inline h-3 w-3" />
                      {relativeTime(entry.timestamp)}
                    </td>
                    <td className="max-w-[180px] truncate px-5 py-3 text-codey-text-dim">
                      {entry.trigger}
                    </td>
                    <td className="px-5 py-3 font-medium text-codey-text">
                      {entry.component}
                    </td>
                    <td className="px-5 py-3 font-mono text-codey-text-dim">
                      {entry.stressBefore.toFixed(2)}
                    </td>
                    <td
                      className={`px-5 py-3 font-mono font-medium ${
                        entry.stressAfter < entry.stressBefore
                          ? "text-codey-green"
                          : "text-codey-red"
                      }`}
                    >
                      {entry.stressAfter.toFixed(2)}
                    </td>
                    <td className="px-5 py-3">
                      {entry.rolledBack ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-codey-red/20 px-2.5 py-0.5 text-xs font-medium text-codey-red">
                          <RotateCcw className="h-3 w-3" />
                          Rolled back
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-codey-green/20 px-2.5 py-0.5 text-xs font-medium text-codey-green">
                          <Check className="h-3 w-3" />
                          Applied
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right text-xs text-codey-text-muted">
                      {entry.creditsUsed}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
