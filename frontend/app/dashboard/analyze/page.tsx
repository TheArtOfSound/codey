"use client";

import { useState, useRef, useCallback } from "react";
import Link from "next/link";
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
  score: number;
  grade: string;
  phase: string;
  metrics: {
    kappa: number;
    sigma: number;
    es: number;
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

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AnalyzePage() {
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
      // Simulated analysis call - replace with actual API endpoint
      // In production: upload files, then call api.analyzeUpload(formData)
      await new Promise((resolve) => setTimeout(resolve, 3000));

      // Mock result for structure demonstration
      setResult({
        score: 0.72,
        grade: "B",
        phase: "Healthy",
        metrics: {
          kappa: 0.68,
          sigma: 0.75,
          es: 0.72,
          coupling: 0.31,
          complexity: 0.45,
        },
        stressComponents: [
          { name: "AuthService", stress: 0.87, type: "service", file: "src/auth/service.ts" },
          { name: "UserController", stress: 0.79, type: "controller", file: "src/users/controller.ts" },
          { name: "PaymentProcessor", stress: 0.74, type: "service", file: "src/payments/processor.ts" },
          { name: "DatabaseAdapter", stress: 0.71, type: "adapter", file: "src/db/adapter.ts" },
          { name: "NotificationHub", stress: 0.68, type: "service", file: "src/notifications/hub.ts" },
          { name: "SessionManager", stress: 0.65, type: "manager", file: "src/session/manager.ts" },
          { name: "CacheLayer", stress: 0.61, type: "layer", file: "src/cache/layer.ts" },
          { name: "RouterMiddleware", stress: 0.58, type: "middleware", file: "src/router/middleware.ts" },
          { name: "LoggerService", stress: 0.44, type: "service", file: "src/logger/service.ts" },
          { name: "ConfigLoader", stress: 0.32, type: "utility", file: "src/config/loader.ts" },
        ],
        recommendations: [
          {
            component: "AuthService",
            message: "High coupling with 6 other services — consider extracting shared auth logic into a middleware",
            severity: "high",
          },
          {
            component: "PaymentProcessor",
            message: "Cyclomatic complexity of 34 in processPayment() — break into smaller transaction steps",
            severity: "high",
          },
          {
            component: "UserController",
            message: "Direct database calls bypass the service layer — route through UserService",
            severity: "medium",
          },
          {
            component: "DatabaseAdapter",
            message: "No connection pooling configured — add pool limits for production stability",
            severity: "medium",
          },
          {
            component: "NotificationHub",
            message: "Synchronous event dispatching may cause cascading delays — consider async queue",
            severity: "low",
          },
        ],
      });
    } catch (err) {
      setError("Analysis failed. Please check your files and try again.");
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
              Structural health assessment for {files.length} file{files.length !== 1 ? "s" : ""}
            </p>
          </div>
          <button
            onClick={handleReset}
            className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
          >
            New analysis
          </button>
        </div>

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
                {result.stressComponents.map((comp, i) => (
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
                        href={`/dashboard/prompt?fix=${encodeURIComponent(comp.name)}&file=${encodeURIComponent(comp.file)}`}
                        className="inline-flex items-center gap-1 rounded-lg border border-codey-green/30 bg-codey-green/10 px-3 py-1 text-xs font-medium text-codey-green hover:bg-codey-green/20"
                      >
                        <Wrench className="h-3 w-3" />
                        Fix this
                      </Link>
                    </td>
                  </tr>
                ))}
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
            {result.recommendations.map((rec, i) => (
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
                  href={`/dashboard/prompt?fix=${encodeURIComponent(rec.component)}`}
                  className="shrink-0 text-xs text-codey-green hover:underline"
                >
                  Fix <ArrowRight className="ml-0.5 inline h-3 w-3" />
                </Link>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Upload View ─────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-codey-text">Analyze Codebase</h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          Upload files for structural health analysis. Get a full report with complexity scores and recommendations.
        </p>
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
