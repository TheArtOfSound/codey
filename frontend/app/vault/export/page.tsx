"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  Download,
  GitBranch,
  Webhook,
  Package,
  ChevronDown,
  Check,
  X,
  Loader2,
  Clock,
  ExternalLink,
  FileArchive,
  RefreshCw,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ExportProject {
  id: string;
  name: string;
  language: string;
}

interface ExportHistoryEntry {
  id: string;
  project_name: string;
  export_type: "download" | "github" | "webhook";
  status: "completed" | "pending" | "failed";
  created_at: string;
  download_url: string | null;
  destination: string;
}

type ExportType = "download" | "github" | "webhook";

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusBadge(status: ExportHistoryEntry["status"]): { label: string; color: string } {
  switch (status) {
    case "completed":
      return { label: "Completed", color: "bg-codey-green/20 text-codey-green" };
    case "pending":
      return { label: "Pending", color: "bg-codey-yellow/20 text-codey-yellow" };
    case "failed":
      return { label: "Failed", color: "bg-codey-red/20 text-codey-red" };
  }
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function ExportCenterPage() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<ExportProject[]>([]);
  const [history, setHistory] = useState<ExportHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  // Export form state
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [exportType, setExportType] = useState<ExportType>("download");
  const [githubRepo, setGithubRepo] = useState("");
  const [githubBranch, setGithubBranch] = useState("main");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportSuccess, setExportSuccess] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [projData, histData] = await Promise.all([
          api.get<ExportProject[]>("/vault/projects"),
          api.get<ExportHistoryEntry[]>("/vault/exports"),
        ]);
        setProjects(projData);
        setHistory(histData);
      } catch {
        setProjects([
          { id: "1", name: "codey-frontend", language: "TypeScript" },
          { id: "2", name: "api-service", language: "Python" },
          { id: "3", name: "nfet-analyzer", language: "Rust" },
        ]);
        setHistory([
          {
            id: "e1",
            project_name: "codey-frontend",
            export_type: "download",
            status: "completed",
            created_at: new Date(Date.now() - 86400_000).toISOString(),
            download_url: "#",
            destination: "codey-frontend-v3.zip",
          },
          {
            id: "e2",
            project_name: "api-service",
            export_type: "github",
            status: "completed",
            created_at: new Date(Date.now() - 172800_000).toISOString(),
            download_url: null,
            destination: "user/api-service (main)",
          },
        ]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function handleExport() {
    if (!selectedProject) return;
    setExporting(true);
    setExportError(null);

    try {
      const payload: Record<string, string> = {
        project_id: selectedProject,
        export_type: exportType,
      };

      if (exportType === "github") {
        payload.github_repo = githubRepo;
        payload.github_branch = githubBranch;
      } else if (exportType === "webhook") {
        payload.webhook_url = webhookUrl;
      }

      await api.post("/vault/exports", payload);
      setExportSuccess(true);
      setTimeout(() => setExportSuccess(false), 3000);

      // Refresh history
      try {
        const histData = await api.get<ExportHistoryEntry[]>("/vault/exports");
        setHistory(histData);
      } catch {}
    } catch (err) {
      setExportError("Export failed. Please check your configuration and try again.");
    } finally {
      setExporting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
      </div>
    );
  }

  const selectedProjectName = projects.find((p) => p.id === selectedProject)?.name;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-codey-text">
          <Package className="h-6 w-6 text-codey-green" />
          Export Center
        </h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          Export your projects as a zip, push to GitHub, or send via webhook.
        </p>
      </div>

      {/* Export Form */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border/50 px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">New Export</h2>
        </div>
        <div className="space-y-5 px-5 py-5">
          {/* Project selector */}
          <div>
            <label className="text-xs font-medium text-codey-text-dim">Project</label>
            <div className="relative mt-1">
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="w-full appearance-none rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 pr-10 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
              >
                <option value="">Select a project</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.language})
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-codey-text-muted" />
            </div>
          </div>

          {/* Export type */}
          <div>
            <label className="text-xs font-medium text-codey-text-dim">Export Type</label>
            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
              {[
                { type: "download" as ExportType, icon: FileArchive, label: "Download", desc: "ZIP archive" },
                { type: "github" as ExportType, icon: GitBranch, label: "GitHub", desc: "Push to repo" },
                { type: "webhook" as ExportType, icon: Webhook, label: "Webhook", desc: "API endpoint" },
              ].map(({ type, icon: Icon, label, desc }) => (
                <button
                  key={type}
                  onClick={() => setExportType(type)}
                  className={`flex items-center gap-3 rounded-lg border p-4 text-left transition-all ${
                    exportType === type
                      ? "border-codey-green bg-codey-green/5"
                      : "border-codey-border bg-codey-bg hover:border-codey-border-light"
                  }`}
                >
                  <Icon className={`h-5 w-5 ${exportType === type ? "text-codey-green" : "text-codey-text-dim"}`} />
                  <div>
                    <p className={`text-sm font-medium ${exportType === type ? "text-codey-green" : "text-codey-text"}`}>
                      {label}
                    </p>
                    <p className="text-xs text-codey-text-muted">{desc}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* GitHub config */}
          {exportType === "github" && (
            <div className="space-y-3 rounded-lg border border-codey-border/50 bg-codey-bg p-4">
              <div>
                <label className="text-xs font-medium text-codey-text-dim">Repository</label>
                <input
                  type="text"
                  value={githubRepo}
                  onChange={(e) => setGithubRepo(e.target.value)}
                  placeholder="user/repository"
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-card px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-codey-text-dim">Branch</label>
                <input
                  type="text"
                  value={githubBranch}
                  onChange={(e) => setGithubBranch(e.target.value)}
                  placeholder="main"
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-card px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>
            </div>
          )}

          {/* Webhook config */}
          {exportType === "webhook" && (
            <div className="rounded-lg border border-codey-border/50 bg-codey-bg p-4">
              <label className="text-xs font-medium text-codey-text-dim">Webhook URL</label>
              <input
                type="url"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
                placeholder="https://your-api.com/webhook"
                className="mt-1 w-full rounded-lg border border-codey-border bg-codey-card px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
              />
              <p className="mt-2 text-xs text-codey-text-muted">
                Codey will POST a JSON payload with the project files to this URL.
              </p>
            </div>
          )}

          {/* Error / Success */}
          {exportError && (
            <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {exportError}
            </div>
          )}

          {exportSuccess && (
            <div className="rounded-lg border border-codey-green/30 bg-codey-green-glow px-4 py-3 text-sm text-codey-green">
              <Check className="mr-2 inline h-4 w-4" />
              Export started successfully!
            </div>
          )}

          {/* Export button */}
          <button
            onClick={handleExport}
            disabled={!selectedProject || exporting}
            className="flex items-center gap-2 rounded-lg bg-codey-green px-5 py-2.5 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exporting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Exporting...
              </>
            ) : (
              <>
                <Download className="h-4 w-4" />
                Export {selectedProjectName || "project"}
              </>
            )}
          </button>
        </div>
      </div>

      {/* Export History */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border/50 px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Export History</h2>
        </div>

        {history.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No exports yet. Export a project to see it here.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">Project</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Destination</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry) => {
                  const badge = statusBadge(entry.status);
                  return (
                    <tr
                      key={entry.id}
                      className="border-b border-codey-border/50 transition-colors hover:bg-codey-card-hover"
                    >
                      <td className="px-5 py-3 font-medium text-codey-text">
                        {entry.project_name}
                      </td>
                      <td className="px-5 py-3">
                        <span className="flex items-center gap-1.5 text-xs text-codey-text-dim">
                          {entry.export_type === "download" && <FileArchive className="h-3 w-3" />}
                          {entry.export_type === "github" && <GitBranch className="h-3 w-3" />}
                          {entry.export_type === "webhook" && <Webhook className="h-3 w-3" />}
                          <span className="capitalize">{entry.export_type}</span>
                        </span>
                      </td>
                      <td className="max-w-[200px] truncate px-5 py-3 text-xs text-codey-text-muted">
                        {entry.destination}
                      </td>
                      <td className="px-5 py-3">
                        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${badge.color}`}>
                          {badge.label}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-5 py-3 text-xs text-codey-text-muted">
                        {formatDate(entry.created_at)}
                      </td>
                      <td className="px-5 py-3 text-right">
                        {entry.download_url && entry.status === "completed" ? (
                          <a
                            href={entry.download_url}
                            className="inline-flex items-center gap-1 text-xs text-codey-green hover:underline"
                          >
                            <Download className="h-3 w-3" />
                            Download
                          </a>
                        ) : entry.status === "pending" ? (
                          <span className="inline-flex items-center gap-1 text-xs text-codey-yellow">
                            <RefreshCw className="h-3 w-3 animate-spin" />
                            Processing
                          </span>
                        ) : (
                          <span className="text-xs text-codey-text-muted">--</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
