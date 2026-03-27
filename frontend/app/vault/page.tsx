"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import Link from "next/link";
import {
  FolderCode,
  Clock,
  FileCode,
  GitBranch,
  ChevronRight,
  ChevronDown,
  Folder,
  File,
  RotateCcw,
  Download,
  Search,
  Loader2,
  Activity,
  Hash,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface VaultProject {
  id: string;
  name: string;
  language: string;
  last_active: string;
  line_count: number;
  nfet_score: number | null;
  session_count: number;
  versions: VaultVersion[];
  file_tree: FileNode[];
}

interface VaultVersion {
  id: string;
  version: number;
  created_at: string;
  nfet_score: number | null;
  prompt_summary: string;
  lines_changed: number;
}

interface FileNode {
  name: string;
  type: "file" | "directory";
  children?: FileNode[];
  lines?: number;
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
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

function nfetPhase(score: number | null): { label: string; color: string; bg: string } {
  if (score === null) return { label: "N/A", color: "text-codey-text-dim", bg: "bg-codey-card" };
  if (score >= 0.7) return { label: "RIDGE", color: "text-codey-green", bg: "bg-codey-green/20" };
  if (score >= 0.4) return { label: "CAUTION", color: "text-codey-yellow", bg: "bg-codey-yellow/20" };
  return { label: "CRITICAL", color: "text-codey-red", bg: "bg-codey-red/20" };
}

function languageIcon(lang: string): string {
  const icons: Record<string, string> = {
    python: "py",
    typescript: "ts",
    javascript: "js",
    rust: "rs",
    go: "go",
    java: "jv",
    ruby: "rb",
    cpp: "c+",
    csharp: "c#",
  };
  return icons[lang.toLowerCase()] || lang.slice(0, 2).toLowerCase();
}

function languageColor(lang: string): string {
  const colors: Record<string, string> = {
    python: "bg-blue-500/20 text-blue-400",
    typescript: "bg-blue-600/20 text-blue-300",
    javascript: "bg-yellow-500/20 text-yellow-400",
    rust: "bg-orange-500/20 text-orange-400",
    go: "bg-cyan-500/20 text-cyan-400",
    java: "bg-red-500/20 text-red-400",
    ruby: "bg-red-600/20 text-red-300",
  };
  return colors[lang.toLowerCase()] || "bg-codey-card-hover text-codey-text-dim";
}

// ── File Tree Component ───────────────────────────────────────────────────────

function FileTreeNode({ node, depth = 0 }: { node: FileNode; depth?: number }) {
  const [expanded, setExpanded] = useState(depth < 2);

  if (node.type === "directory") {
    return (
      <div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0" />
          )}
          <Folder className="h-3.5 w-3.5 shrink-0 text-codey-yellow" />
          <span className="truncate">{node.name}</span>
        </button>
        {expanded && node.children && (
          <div>
            {node.children.map((child, i) => (
              <FileTreeNode key={`${child.name}-${i}`} node={child} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-1.5 rounded px-2 py-1 text-sm text-codey-text-dim"
      style={{ paddingLeft: `${depth * 16 + 8}px` }}
    >
      <span className="w-3" />
      <File className="h-3.5 w-3.5 shrink-0 text-codey-text-muted" />
      <span className="truncate">{node.name}</span>
      {node.lines !== undefined && (
        <span className="ml-auto text-xs text-codey-text-muted">{node.lines}L</span>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function VaultPage() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<VaultProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedProject, setSelectedProject] = useState<VaultProject | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number>(0);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const data = await api.get<VaultProject[]>("/vault/projects");
        setProjects(data);
      } catch {
        // Demo data
        setProjects([
          {
            id: "1",
            name: "codey-frontend",
            language: "TypeScript",
            last_active: new Date(Date.now() - 3600_000).toISOString(),
            line_count: 12450,
            nfet_score: 0.82,
            session_count: 14,
            versions: [
              { id: "v3", version: 3, created_at: new Date(Date.now() - 3600_000).toISOString(), nfet_score: 0.82, prompt_summary: "Add settings page with billing", lines_changed: 340 },
              { id: "v2", version: 2, created_at: new Date(Date.now() - 86400_000).toISOString(), nfet_score: 0.78, prompt_summary: "Implement dashboard and sessions", lines_changed: 890 },
              { id: "v1", version: 1, created_at: new Date(Date.now() - 172800_000).toISOString(), nfet_score: 0.65, prompt_summary: "Initial scaffold with auth", lines_changed: 2100 },
            ],
            file_tree: [
              { name: "app", type: "directory", children: [
                { name: "page.tsx", type: "file", lines: 85 },
                { name: "layout.tsx", type: "file", lines: 42 },
                { name: "dashboard", type: "directory", children: [
                  { name: "page.tsx", type: "file", lines: 310 },
                  { name: "prompt", type: "directory", children: [{ name: "page.tsx", type: "file", lines: 280 }] },
                ]},
              ]},
              { name: "lib", type: "directory", children: [
                { name: "api.ts", type: "file", lines: 220 },
                { name: "auth.ts", type: "file", lines: 165 },
              ]},
              { name: "components", type: "directory", children: [
                { name: "layout", type: "directory", children: [
                  { name: "Navbar.tsx", type: "file", lines: 180 },
                  { name: "DashboardLayout.tsx", type: "file", lines: 95 },
                ]},
              ]},
            ],
          },
          {
            id: "2",
            name: "api-service",
            language: "Python",
            last_active: new Date(Date.now() - 7200_000).toISOString(),
            line_count: 8320,
            nfet_score: 0.74,
            session_count: 9,
            versions: [
              { id: "v2", version: 2, created_at: new Date(Date.now() - 7200_000).toISOString(), nfet_score: 0.74, prompt_summary: "Add NFET analysis endpoints", lines_changed: 450 },
              { id: "v1", version: 1, created_at: new Date(Date.now() - 259200_000).toISOString(), nfet_score: 0.61, prompt_summary: "FastAPI scaffold with auth routes", lines_changed: 1800 },
            ],
            file_tree: [
              { name: "src", type: "directory", children: [
                { name: "main.py", type: "file", lines: 120 },
                { name: "routes", type: "directory", children: [
                  { name: "auth.py", type: "file", lines: 180 },
                  { name: "sessions.py", type: "file", lines: 220 },
                ]},
                { name: "models", type: "directory", children: [
                  { name: "user.py", type: "file", lines: 65 },
                  { name: "session.py", type: "file", lines: 85 },
                ]},
              ]},
            ],
          },
          {
            id: "3",
            name: "nfet-analyzer",
            language: "Rust",
            last_active: new Date(Date.now() - 432000_000).toISOString(),
            line_count: 3200,
            nfet_score: 0.91,
            session_count: 4,
            versions: [
              { id: "v1", version: 1, created_at: new Date(Date.now() - 432000_000).toISOString(), nfet_score: 0.91, prompt_summary: "Core NFET analysis engine", lines_changed: 3200 },
            ],
            file_tree: [
              { name: "src", type: "directory", children: [
                { name: "main.rs", type: "file", lines: 45 },
                { name: "analyzer.rs", type: "file", lines: 680 },
                { name: "parser.rs", type: "file", lines: 420 },
              ]},
              { name: "Cargo.toml", type: "file", lines: 28 },
            ],
          },
        ]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const filteredProjects = projects.filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase())
  );

  async function handleRestore(versionId: string) {
    if (!selectedProject) return;
    setRestoring(true);
    try {
      await api.post(`/vault/projects/${selectedProject.id}/restore`, { version_id: versionId });
    } catch {}
    setTimeout(() => setRestoring(false), 1500);
  }

  const currentVersion = selectedProject?.versions[selectedVersion];
  const currentNfet = currentVersion ? nfetPhase(currentVersion.nfet_score) : null;

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-codey-text">
            <FolderCode className="h-6 w-6 text-codey-green" />
            Code Vault
          </h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Every version of every project Codey has generated. Browse, compare, and restore.
          </p>
        </div>
        <Link
          href="/vault/export"
          className="flex items-center gap-1.5 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
        >
          <Download className="h-4 w-4" />
          Export Center
        </Link>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-codey-text-muted" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search projects..."
          className="w-full rounded-xl border border-codey-border bg-codey-card py-3 pl-11 pr-4 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
        />
      </div>

      {/* Project Grid / Detail Split */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Project List */}
        <div className={`space-y-3 ${selectedProject ? "lg:col-span-1" : "lg:col-span-3"}`}>
          {selectedProject && (
            <button
              onClick={() => setSelectedProject(null)}
              className="mb-2 flex items-center gap-1 text-xs text-codey-green hover:underline"
            >
              <ChevronRight className="h-3 w-3 rotate-180" />
              All projects
            </button>
          )}

          <div className={`grid gap-3 ${selectedProject ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"}`}>
            {filteredProjects.map((project) => {
              const phase = nfetPhase(project.nfet_score);
              const isSelected = selectedProject?.id === project.id;

              return (
                <button
                  key={project.id}
                  onClick={() => {
                    setSelectedProject(project);
                    setSelectedVersion(0);
                  }}
                  className={`group rounded-xl border p-5 text-left transition-all ${
                    isSelected
                      ? "border-codey-green bg-codey-green/5"
                      : "border-codey-border bg-codey-card hover:border-codey-border-light hover:bg-codey-card-hover"
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className={`flex h-10 w-10 items-center justify-center rounded-lg font-mono text-xs font-bold ${languageColor(project.language)}`}>
                        {languageIcon(project.language)}
                      </div>
                      <div>
                        <h3 className="text-sm font-semibold text-codey-text">{project.name}</h3>
                        <p className="text-xs text-codey-text-muted">{project.language}</p>
                      </div>
                    </div>
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${phase.bg} ${phase.color}`}>
                      {phase.label}
                    </span>
                  </div>

                  <div className="mt-4 flex items-center gap-4 text-xs text-codey-text-muted">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {relativeTime(project.last_active)}
                    </span>
                    <span className="flex items-center gap-1">
                      <Hash className="h-3 w-3" />
                      {project.line_count.toLocaleString()} lines
                    </span>
                    <span className="flex items-center gap-1">
                      <Activity className="h-3 w-3" />
                      {project.session_count} sessions
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {filteredProjects.length === 0 && (
            <div className="rounded-xl border border-codey-border bg-codey-card px-5 py-12 text-center">
              <FolderCode className="mx-auto h-8 w-8 text-codey-text-muted" />
              <p className="mt-2 text-sm text-codey-text-dim">
                {search ? "No projects match your search." : "No projects in your vault yet. Start a prompt to create one."}
              </p>
            </div>
          )}
        </div>

        {/* Project Detail */}
        {selectedProject && (
          <div className="space-y-4 lg:col-span-2">
            {/* Version Timeline */}
            <div className="rounded-xl border border-codey-border bg-codey-card">
              <div className="border-b border-codey-border/50 px-5 py-4">
                <h2 className="text-sm font-semibold text-codey-text">Version Timeline</h2>
              </div>
              <div className="px-5 py-4">
                {/* Horizontal timeline slider */}
                <div className="flex items-center gap-2 overflow-x-auto pb-2">
                  {selectedProject.versions.map((version, idx) => {
                    const vPhase = nfetPhase(version.nfet_score);
                    const isActive = idx === selectedVersion;
                    return (
                      <button
                        key={version.id}
                        onClick={() => setSelectedVersion(idx)}
                        className={`flex shrink-0 flex-col items-center gap-1.5 rounded-lg border px-4 py-3 transition-all ${
                          isActive
                            ? "border-codey-green bg-codey-green/10"
                            : "border-codey-border bg-codey-bg hover:border-codey-border-light"
                        }`}
                      >
                        <span className={`text-xs font-bold ${isActive ? "text-codey-green" : "text-codey-text"}`}>
                          v{version.version}
                        </span>
                        <span className={`inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-medium ${vPhase.bg} ${vPhase.color}`}>
                          {version.nfet_score !== null ? (version.nfet_score * 100).toFixed(0) : "--"}
                        </span>
                        <span className="text-[10px] text-codey-text-muted">
                          {new Date(version.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* Version Details */}
                {currentVersion && (
                  <div className="mt-4 rounded-lg border border-codey-border/50 bg-codey-bg p-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <h3 className="text-sm font-medium text-codey-text">
                          Version {currentVersion.version}
                        </h3>
                        <p className="mt-1 text-xs text-codey-text-dim">
                          {currentVersion.prompt_summary}
                        </p>
                        <div className="mt-2 flex items-center gap-3 text-xs text-codey-text-muted">
                          <span>{relativeTime(currentVersion.created_at)}</span>
                          <span>{currentVersion.lines_changed} lines changed</span>
                          {currentNfet && (
                            <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 font-medium ${currentNfet.bg} ${currentNfet.color}`}>
                              NFET: {currentNfet.label}
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => handleRestore(currentVersion.id)}
                        disabled={restoring || selectedVersion === 0}
                        className="flex items-center gap-1.5 rounded-lg border border-codey-border px-3 py-1.5 text-xs font-medium text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-green disabled:opacity-40"
                      >
                        {restoring ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <RotateCcw className="h-3 w-3" />
                        )}
                        Restore
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Sessions */}
            <div className="rounded-xl border border-codey-border bg-codey-card">
              <div className="border-b border-codey-border/50 px-5 py-4">
                <h2 className="text-sm font-semibold text-codey-text">Sessions</h2>
              </div>
              <div className="divide-y divide-codey-border/50">
                {selectedProject.versions.map((v) => (
                  <div key={v.id} className="flex items-center justify-between px-5 py-3 transition-colors hover:bg-codey-card-hover">
                    <div>
                      <p className="text-sm text-codey-text">{v.prompt_summary}</p>
                      <p className="text-xs text-codey-text-muted">{relativeTime(v.created_at)}</p>
                    </div>
                    <span className="text-xs text-codey-text-dim">v{v.version}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* File Tree */}
            <div className="rounded-xl border border-codey-border bg-codey-card">
              <div className="border-b border-codey-border/50 px-5 py-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-codey-text">File Tree</h2>
                  <Link
                    href="/vault/export"
                    className="flex items-center gap-1 text-xs text-codey-green hover:underline"
                  >
                    <Download className="h-3 w-3" />
                    Export
                  </Link>
                </div>
              </div>
              <div className="max-h-80 overflow-y-auto px-2 py-3">
                {selectedProject.file_tree.map((node, i) => (
                  <FileTreeNode key={`${node.name}-${i}`} node={node} />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
