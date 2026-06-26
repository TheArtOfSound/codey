"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import Link from "next/link";
import { api, type Repo } from "@/lib/api";
import {
  Upload,
  FileCode,
  X,
  Loader2,
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Shield,
  Wrench,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AnalysisResult {
  source: "upload" | "repo";
  repoId?: string;
  repoName?: string;
  summary?: string;
  score: number;
  grade: string;
  phase: string;
  metrics: {
    coherence: number;
    stability: number;
    health: number;
    coupling: number;
    complexity: number;
  };
  stressComponents: Array<{
    name: string;
    stress: number;
    type: string;
    file: string;
  }>;
  recommendations: Array<{
    component: string;
    message: string;
    severity: "high" | "medium" | "low";
  }>;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function phaseStyle(phase: string): { color: string; bg: string } {
  switch (phase.toUpperCase()) {
    case "RIDGE":
    case "HEALTHY":
      return { color: "text-codey-green", bg: "bg-codey-green/20" };
    case "CAUTION":
    case "WATCH":
      return { color: "text-codey-yellow", bg: "bg-codey-yellow/20" };
    case "CRITICAL":
    case "AT RISK":
      return { color: "text-codey-red", bg: "bg-codey-red/20" };
    default:
      return { color: "text-codey-text-dim", bg: "bg-codey-card" };
  }
}

function severityStyle(severity: "high" | "medium" | "low") {
  switch (severity) {
    case "high":
      return "border-codey-red/30 bg-codey-red-glow text-codey-red";
    case "medium":
      return "border-codey-yellow/30 bg-codey-yellow-glow text-codey-yellow";
    case "low":
      return "border-codey-border bg-codey-card text-codey-text-dim";
  }
}

function repoPromptHref(result: AnalysisResult): string {
  const prompt =
    result.source === "repo"
      ? `Implement the highest-value hardening change for ${result.repoName || "this repository"} using the connected repo files as ground truth. Update the most relevant existing file first, explain what changed, and preserve current behavior outside the target area.`
      : `Fix the highest-risk structural issue found in this upload.`;
  const repoQuery = result.repoId ? `&repo=${encodeURIComponent(result.repoId)}` : "";
  return `/dashboard/prompt?fix=${encodeURIComponent(prompt)}${repoQuery}`;
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AnalyzePage() {
  const [files, setFiles] = useState<File[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<string>("");
  const [dragOver, setDragOver] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .getRepos()
      .then((repoData) => {
        setRepos(repoData);
        if (repoData.length > 0) {
          setSelectedRepoId(repoData[0].id);
        }
      })
      .catch(() => {});
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files);
    setFiles((prev) => [...prev, ...dropped]);
  }, []);

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) {
      setFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
    }
  }

  function removeFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleAnalyze() {
    if (files.length === 0) return;
    setAnalyzing(true);
    setError(null);

    try {
      const analysis = await api.analyzeUpload(files);
      const score = analysis.report.health_score;
      setResult({
        source: "upload",
        summary: analysis.report.summary,
        score,
        grade: score >= 0.85 ? "A" : score >= 0.7 ? "B" : score >= 0.55 ? "C" : "D",
        phase: analysis.report.phase,
        metrics: {
          coherence: analysis.report.coherence,
          stability: analysis.report.stability,
          health: analysis.report.health_score,
          coupling: analysis.report.mean_coupling,
          complexity: 1 - analysis.report.mean_cohesion,
        },
        stressComponents: analysis.report.top_components.map((component) => ({
          name: component.name,
          stress: component.stress,
          type: `cascade depth ${component.cascade_depth}`,
          file: component.file_path,
        })),
        recommendations: analysis.recommendations.map((message) => ({
          component: analysis.report.highest_stress_component,
          message,
          severity: /critical|immediate|high/i.test(message)
            ? "high"
            : /watch|refactor|coupling|stability/i.test(message)
              ? "medium"
              : "low",
        })),
      });
    } catch (err) {
      console.error("Analysis failed:", err);
      const _d = (err as { detail?: string })?.detail;
      setError(_d || "Analysis failed. Please check your files and try again.");
    } finally {
      setAnalyzing(false);
    }
  }

  async function handleAnalyzeRepo() {
    if (!selectedRepoId) return;
    setAnalyzing(true);
    setError(null);

    try {
      const [summary, candidateData] = await Promise.all([
        api.get<{
          repo_id: string;
          full_name: string | null;
          phase: string;
          global_es: number;
          global_kappa: number;
          global_sigma: number;
          top_hotspots: Array<{
            name: string;
            file_path: string;
            risk_score: number;
            kind: string;
          }>;
          highest_stress_component: string;
          highest_stress_value: number;
          hotspot_count: number;
        }>(`/repos/${selectedRepoId}/nfet/summary`),
        api.post<{
          repo_id: string;
          candidates: Array<{
            title: string;
            description: string;
            target_file_path: string;
            predicted_repo_es_delta: number;
            risk: number;
            reasons: string[];
          }>;
        }>(`/repos/${selectedRepoId}/nfet/candidates`, {
          goal: "Find the highest-value repo fixes for a dashboard user",
          limit: 5,
        }),
      ]);

      const score = summary.global_es;
      setResult({
        source: "repo",
        repoId: summary.repo_id,
        repoName: summary.full_name || repos.find((repo) => repo.id === selectedRepoId)?.name || "Repository",
        summary: `Global sigma ${summary.global_sigma.toFixed(3)} · global kappa ${summary.global_kappa.toFixed(3)} · ${summary.hotspot_count} hotspot${summary.hotspot_count === 1 ? "" : "s"} detected.`,
        score,
        grade: score >= 0.85 ? "A" : score >= 0.7 ? "B" : score >= 0.55 ? "C" : "D",
        phase: summary.phase,
        metrics: {
          coherence: Math.max(0, 1 - summary.global_sigma),
          stability: summary.global_es,
          health: summary.global_es,
          coupling: summary.global_kappa,
          complexity: Math.min(1, summary.global_sigma),
        },
        stressComponents: summary.top_hotspots.map((component) => ({
          name: component.name,
          stress: component.risk_score,
          type: component.kind,
          file: component.file_path,
        })),
        recommendations: candidateData.candidates.map((candidate) => ({
          component: candidate.title,
          message: `${candidate.description} (predicted ES delta ${candidate.predicted_repo_es_delta.toFixed(3)}, risk ${candidate.risk.toFixed(2)})`,
          severity: candidate.risk >= 0.7 ? "high" : candidate.risk >= 0.4 ? "medium" : "low",
        })),
      });
    } catch (err) {
      console.error("Repo analysis failed:", err);
      const _d = (err as { detail?: string })?.detail;
      setError(_d || "Repo analysis failed. Check repo access and try again.");
    } finally {
      setAnalyzing(false);
    }
  }

  function handleReset() {
    setFiles([]);
    setResult(null);
    setError(null);
  }

  // ── Results View ────────────────────────────────────────────────────────────

  if (result) {
    const pStyle = phaseStyle(result.phase);

    return (
      <div className="mx-auto max-w-5xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-codey-text">Analysis Report</h1>
            <p className="mt-1 text-sm text-codey-text-dim">
              {result.source === "repo"
                ? `Structural health assessment for ${result.repoName}`
                : `Structural health assessment for ${files.length} file${files.length !== 1 ? "s" : ""}`}
            </p>
          </div>
          <button
            onClick={handleReset}
            className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
          >
            New analysis
          </button>
        </div>

        {result.summary && (
          <div className="rounded-xl border border-codey-border bg-codey-card p-5">
            <h2 className="text-sm font-semibold text-codey-text">Summary</h2>
            <p className="mt-2 text-sm text-codey-text-dim">{result.summary}</p>
            <div className="mt-4">
              <Link
                href={repoPromptHref(result)}
                className="inline-flex items-center gap-2 rounded-lg border border-codey-green/30 bg-codey-green/10 px-4 py-2 text-sm font-medium text-codey-green hover:bg-codey-green/20"
              >
                <Wrench className="h-4 w-4" />
                Send findings to intervention console
              </Link>
            </div>
          </div>
        )}

        {/* ── Report Card ──────────────────────────────────────────── */}
        <div className="grid gap-4 sm:grid-cols-3">
          {/* Grade */}
          <div className="rounded-xl border border-codey-border bg-codey-card p-5 text-center">
            <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
              Grade
            </p>
            <p
              className={`mt-2 text-5xl font-black ${
                result.grade === "A" || result.grade === "B"
                  ? "text-codey-green"
                  : result.grade === "C"
                    ? "text-codey-yellow"
                    : "text-codey-red"
              }`}
            >
              {result.grade}
            </p>
          </div>

          {/* Phase */}
          <div className="rounded-xl border border-codey-border bg-codey-card p-5 text-center">
            <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
              Health Status
            </p>
            <p className={`mt-2 text-2xl font-bold ${pStyle.color}`}>
              {result.phase}
            </p>
          </div>

          {/* Health Score */}
          <div className="rounded-xl border border-codey-border bg-codey-card p-5 text-center">
            <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
              Health Score
            </p>
            <p className="mt-2 text-3xl font-bold text-codey-text">
              {result.score.toFixed(3)}
            </p>
          </div>
        </div>

        {/* ── Metrics ──────────────────────────────────────────────── */}
        <div className="rounded-xl border border-codey-border bg-codey-card p-5">
          <h2 className="text-sm font-semibold text-codey-text">Metrics</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {Object.entries(result.metrics).map(([key, value]) => (
              <div key={key} className="rounded-lg bg-codey-bg p-3">
                <p className="text-xs text-codey-text-muted capitalize">{key}</p>
                <p className="mt-1 text-lg font-bold text-codey-text">{value.toFixed(3)}</p>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-codey-card-hover">
                  <div
                    className={`h-full rounded-full ${
                      key === "coupling" || key === "complexity"
                        ? value > 0.7
                          ? "bg-codey-red"
                          : value > 0.4
                            ? "bg-codey-yellow"
                            : "bg-codey-green"
                        : value > 0.7
                          ? "bg-codey-green"
                          : value > 0.4
                            ? "bg-codey-yellow"
                            : "bg-codey-red"
                    }`}
                    style={{ width: `${value * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Top 10 Stress Components ─────────────────────────────── */}
        <div className="rounded-xl border border-codey-border bg-codey-card">
          <div className="border-b border-codey-border px-5 py-4">
            <h2 className="text-sm font-semibold text-codey-text">Top 10 Stress Components</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">#</th>
                  <th className="px-5 py-3 font-medium">Component</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Stress</th>
                  <th className="px-5 py-3 font-medium w-48">Level</th>
                  <th className="px-5 py-3 font-medium text-right">Action</th>
                </tr>
              </thead>
	              <tbody>
	                {result.stressComponents.length > 0 ? (
	                  result.stressComponents.map((comp, i) => (
	                    <tr
	                      key={i}
	                      className="border-b border-codey-border/50 hover:bg-codey-card-hover"
	                    >
	                      <td className="px-5 py-3 text-codey-text-muted">{i + 1}</td>
	                      <td className="px-5 py-3">
	                        <div>
	                          <span className="font-medium text-codey-text">{comp.name}</span>
	                          <p className="font-mono text-xs text-codey-text-muted">{comp.file}</p>
	                        </div>
	                      </td>
	                      <td className="px-5 py-3 capitalize text-codey-text-dim">{comp.type}</td>
	                      <td className="px-5 py-3 font-mono text-codey-text">{comp.stress.toFixed(2)}</td>
	                      <td className="px-5 py-3">
	                        <div className="flex items-center gap-2">
	                          <div className="h-2 flex-1 overflow-hidden rounded-full bg-codey-bg">
	                            <div
	                              className={`h-full rounded-full ${
	                                comp.stress > 0.7
	                                  ? "bg-codey-red"
	                                  : comp.stress > 0.5
	                                    ? "bg-codey-yellow"
	                                    : "bg-codey-green"
	                              }`}
	                              style={{ width: `${comp.stress * 100}%` }}
	                            />
	                          </div>
	                        </div>
	                      </td>
	                      <td className="px-5 py-3 text-right">
	                        <Link
	                          href={`/dashboard/prompt?fix=${encodeURIComponent(comp.name)}&file=${encodeURIComponent(comp.file)}${result.repoId ? `&repo=${encodeURIComponent(result.repoId)}` : ""}`}
	                          className="inline-flex items-center gap-1 rounded-lg border border-codey-green/30 bg-codey-green/10 px-3 py-1 text-xs font-medium text-codey-green hover:bg-codey-green/20"
	                        >
	                          <Wrench className="h-3 w-3" />
	                          Fix this
	                        </Link>
	                      </td>
	                    </tr>
	                  ))
	                ) : (
	                  <tr>
	                    <td colSpan={6} className="px-5 py-8 text-center text-sm text-codey-text-dim">
	                      No single hotspot crossed the display threshold. You can still send this repo analysis into the intervention console for a targeted hardening pass.
	                    </td>
	                  </tr>
	                )}
	              </tbody>
	            </table>
	          </div>
        </div>

        {/* ── Recommendations ──────────────────────────────────────── */}
        <div className="rounded-xl border border-codey-border bg-codey-card">
          <div className="border-b border-codey-border px-5 py-4">
            <h2 className="text-sm font-semibold text-codey-text">Recommendations</h2>
          </div>
          <div className="divide-y divide-codey-border/50">
	            {result.recommendations.length > 0 ? (
	              result.recommendations.map((rec, i) => (
	                <div key={i} className="flex items-start gap-3 px-5 py-4">
	                  <div
	                    className={`mt-0.5 shrink-0 rounded-full p-1 ${
	                      rec.severity === "high"
	                        ? "bg-codey-red/20 text-codey-red"
	                        : rec.severity === "medium"
	                          ? "bg-codey-yellow/20 text-codey-yellow"
	                          : "bg-codey-card-hover text-codey-text-dim"
	                    }`}
	                  >
	                    <AlertTriangle className="h-3.5 w-3.5" />
	                  </div>
	                  <div className="flex-1">
	                    <p className="text-sm text-codey-text">
	                      <span className="font-semibold">{rec.component}</span>
	                      {" — "}
	                      {rec.message}
	                    </p>
	                  </div>
	                  <Link
	                    href={`/dashboard/prompt?fix=${encodeURIComponent(rec.component)}${result.repoId ? `&repo=${encodeURIComponent(result.repoId)}` : ""}`}
	                    className="shrink-0 text-xs text-codey-green hover:underline"
	                  >
	                    Fix <ArrowRight className="ml-0.5 inline h-3 w-3" />
	                  </Link>
	                </div>
	              ))
	            ) : (
	              <div className="flex items-center justify-between px-5 py-4 text-sm text-codey-text-dim">
	                <span>No ranked interventions were returned for this run.</span>
	                <Link href={repoPromptHref(result)} className="text-codey-green hover:underline">
	                  Open in console <ArrowRight className="ml-0.5 inline h-3 w-3" />
	                </Link>
	              </div>
	            )}
          </div>
        </div>
      </div>
    );
  }

  // ── Upload View ─────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-codey-text">Repo Scan</h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          Upload files or run NFET analysis against a connected repo. Use the findings to hand off directly into the intervention console.
        </p>
      </div>

      <div className="rounded-xl border border-codey-border bg-codey-card p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-codey-text">Connected Repo Analysis</h2>
            <p className="mt-1 text-sm text-codey-text-dim">
              Run the live NFET controller against a connected repository and send concrete fixes into the intervention console.
            </p>
          </div>
          <div className="flex w-full flex-col gap-3 sm:flex-row lg:w-auto">
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
              disabled={repos.length === 0 || analyzing}
              className="rounded-lg border border-codey-border bg-codey-bg px-3 py-2 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {repos.length === 0 ? (
                <option value="">No connected repos</option>
              ) : (
                repos.map((repo) => (
                  <option key={repo.id} value={repo.id}>
                    {repo.name}
                  </option>
                ))
              )}
            </select>
            <button
              onClick={() => void handleAnalyzeRepo()}
              disabled={!selectedRepoId || analyzing}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-codey-green/30 bg-codey-green/10 px-4 py-2 text-sm font-medium text-codey-green transition-colors hover:bg-codey-green/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {analyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Shield className="h-4 w-4" />}
              Analyze repo
            </button>
          </div>
        </div>
        {repos.length === 0 && (
          <p className="mt-4 text-sm text-codey-text-dim">
            No connected repos yet. Connect one from{" "}
            <Link href="/dashboard/repos" className="text-codey-green hover:underline">
              Repositories
            </Link>
            .
          </p>
        )}
      </div>

      {/* ── Drag & Drop Zone ───────────────────────────────────────── */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`cursor-pointer rounded-2xl border-2 border-dashed p-12 text-center transition-all ${
          dragOver
            ? "border-codey-green bg-codey-green/5"
            : "border-codey-border hover:border-codey-border-light hover:bg-codey-card/50"
        }`}
      >
        <Upload
          className={`mx-auto h-10 w-10 ${
            dragOver ? "text-codey-green" : "text-codey-text-muted"
          }`}
        />
        <p className="mt-4 text-base font-medium text-codey-text">
          Drop your codebase here or <span className="text-codey-green">click to browse</span>
        </p>
        <p className="mt-2 text-sm text-codey-text-dim">
          Accepts .zip, .py, .js, .ts, .java, .go, .rs, and more
        </p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".zip,.py,.js,.jsx,.ts,.tsx,.java,.go,.rs,.rb,.php,.css,.html,.json,.yaml,.yml,.md,.txt,.sql,.sh"
          onChange={handleFileSelect}
          className="hidden"
        />
      </div>

      {/* ── File List ──────────────────────────────────────────────── */}
      {files.length > 0 && (
        <div className="rounded-xl border border-codey-border bg-codey-card">
          <div className="border-b border-codey-border px-5 py-3">
            <span className="text-sm font-medium text-codey-text">
              {files.length} file{files.length !== 1 ? "s" : ""} selected
            </span>
          </div>
          <div className="max-h-64 divide-y divide-codey-border/50 overflow-y-auto">
            {files.map((file, i) => (
              <div
                key={i}
                className="flex items-center justify-between px-5 py-2.5 hover:bg-codey-card-hover"
              >
                <div className="flex items-center gap-2">
                  <FileCode className="h-4 w-4 text-codey-text-dim" />
                  <span className="text-sm text-codey-text">{file.name}</span>
                  <span className="text-xs text-codey-text-muted">
                    ({(file.size / 1024).toFixed(1)} KB)
                  </span>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(i);
                  }}
                  className="rounded p-1 text-codey-text-muted hover:bg-codey-card-hover hover:text-codey-red"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Error ──────────────────────────────────────────────────── */}
      {error && (
        <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
          {error}
        </div>
      )}

      {/* ── Analyze Button ─────────────────────────────────────────── */}
      <button
        onClick={handleAnalyze}
        disabled={files.length === 0 || analyzing}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-codey-green px-8 py-3.5 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
      >
        {analyzing ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Analyzing structural health...
          </>
        ) : (
          <>
            <BarChart3 className="h-4 w-4" />
            Analyze
          </>
        )}
      </button>
    </div>
  );
}
