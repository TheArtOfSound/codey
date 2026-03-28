"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import dynamic from "next/dynamic";
import { useAuth } from "@/lib/auth";
import { api, type Repo } from "@/lib/api";
import {
  useSessionStream,
  type CodeChunk,
  type HealthReport,
} from "@/lib/websocket";
import {
  Zap,
  Send,
  Copy,
  Download,
  GitCommit,
  RotateCcw,
  ChevronDown,
  Upload,
  X,
  Check,
  FileCode,
  BookOpen,
  Activity,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Minus,
  Loader2,
} from "lucide-react";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="flex h-96 items-center justify-center rounded-lg border border-codey-border bg-codey-bg">
      <div className="flex items-center gap-2 text-sm text-codey-text-dim">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading editor...
      </div>
    </div>
  ),
});

// ── Types ─────────────────────────────────────────────────────────────────────

type PageState = "input" | "streaming";
type StreamTab = "code" | "explanation" | "health";

const LANGUAGES = [
  { value: "auto", label: "Auto-detect" },
  { value: "python", label: "Python" },
  { value: "javascript", label: "JavaScript" },
  { value: "typescript", label: "TypeScript" },
  { value: "java", label: "Java" },
  { value: "go", label: "Go" },
  { value: "rust", label: "Rust" },
];

// ── Credit estimator ──────────────────────────────────────────────────────────

function estimateCredits(promptLength: number): number {
  if (promptLength === 0) return 0;
  // Base cost: 1 credit. +1 per 200 chars of prompt. Minimum 1.
  return Math.max(1, Math.ceil(promptLength / 200) + 1);
}

// ── Markdown-ish renderer ─────────────────────────────────────────────────────

function RenderMarkdown({ text }: { text: string }) {
  // Simple markdown rendering for streaming explanation text
  const lines = text.split("\n");
  return (
    <div className="prose prose-invert max-w-none space-y-2 text-sm leading-relaxed text-codey-text">
      {lines.map((line, i) => {
        if (line.startsWith("### ")) {
          return (
            <h3 key={i} className="mt-4 text-base font-semibold text-codey-text">
              {line.slice(4)}
            </h3>
          );
        }
        if (line.startsWith("## ")) {
          return (
            <h2 key={i} className="mt-5 text-lg font-bold text-codey-text">
              {line.slice(3)}
            </h2>
          );
        }
        if (line.startsWith("# ")) {
          return (
            <h1 key={i} className="mt-6 text-xl font-bold text-codey-text">
              {line.slice(2)}
            </h1>
          );
        }
        if (line.startsWith("- ") || line.startsWith("* ")) {
          return (
            <li key={i} className="ml-4 list-disc text-codey-text-dim">
              {line.slice(2)}
            </li>
          );
        }
        if (line.startsWith("```")) {
          return null; // Handled by code blocks in real markdown
        }
        if (line.trim() === "") {
          return <div key={i} className="h-2" />;
        }
        // Bold and code inline
        const rendered = line
          .replace(/\*\*(.*?)\*\*/g, '<strong class="text-codey-text font-semibold">$1</strong>')
          .replace(
            /`(.*?)`/g,
            '<code class="rounded bg-codey-card-hover px-1.5 py-0.5 text-xs font-mono text-codey-green">$1</code>'
          );
        return (
          <p
            key={i}
            className="text-codey-text-dim"
            dangerouslySetInnerHTML={{ __html: rendered }}
          />
        );
      })}
    </div>
  );
}

// ── Health Delta Display ──────────────────────────────────────────────────────

function HealthDelta({
  label,
  before,
  after,
}: {
  label: string;
  before: number | undefined;
  after: number | undefined;
}) {
  if (before === undefined || after === undefined) return null;
  const delta = after - before;
  const improved = delta > 0;
  const unchanged = delta === 0;

  return (
    <div className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
      <span className="text-sm text-codey-text-dim">{label}</span>
      <div className="flex items-center gap-3">
        <span className="text-xs text-codey-text-muted">{before.toFixed(3)}</span>
        <ArrowUpDown className="h-3 w-3 text-codey-text-muted" />
        <span className="text-sm font-medium text-codey-text">{after.toFixed(3)}</span>
        {!unchanged && (
          <span
            className={`flex items-center gap-0.5 text-xs font-medium ${
              improved ? "text-codey-green" : "text-codey-red"
            }`}
          >
            {improved ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}
            {Math.abs(delta).toFixed(3)}
          </span>
        )}
        {unchanged && (
          <span className="flex items-center gap-0.5 text-xs text-codey-text-muted">
            <Minus className="h-3 w-3" />
            0
          </span>
        )}
      </div>
    </div>
  );
}

function HealthGrade({ report }: { report: HealthReport }) {
  const gradeColor =
    report.grade === "A" || report.grade === "B"
      ? "text-codey-green"
      : report.grade === "C"
        ? "text-codey-yellow"
        : "text-codey-red";

  return (
    <div className="flex items-center gap-3 rounded-xl border border-codey-border bg-codey-card p-4">
      <div
        className={`flex h-12 w-12 items-center justify-center rounded-lg text-2xl font-black ${gradeColor} bg-codey-bg`}
      >
        {report.grade}
      </div>
      <div>
        <p className="text-sm font-medium text-codey-text">
          Score: {report.score.toFixed(3)}
        </p>
        <p className="text-xs text-codey-text-dim">
          Structural Health Grade
        </p>
      </div>
    </div>
  );
}

// ── Main Prompt Page ──────────────────────────────────────────────────────────

export default function PromptPage() {
  const { user } = useAuth();

  // Input state
  const [prompt, setPrompt] = useState("");
  const [language, setLanguage] = useState("auto");
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // Session / streaming state
  const [pageState, setPageState] = useState<PageState>("input");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<StreamTab>("code");
  const [activeFileIndex, setActiveFileIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const [explanationText, setExplanationText] = useState("");

  // Drag-and-drop state
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // WebSocket stream
  const stream = useSessionStream(sessionId);

  // Load repos on mount
  useEffect(() => {
    api.getRepos().then(setRepos).catch(() => {});
  }, []);

  // Accumulate explanation text from log messages
  useEffect(() => {
    const logMessages = stream.messages.filter((m) => m.type === "log");
    if (logMessages.length > 0) {
      setExplanationText(logMessages.map((m) => m.data as string).join("\n"));
    }
  }, [stream.messages]);

  // Credit estimate
  const estimatedCredits = estimateCredits(prompt.length);
  const hasCredits = (user?.credits_remaining ?? 0) > 0;
  const canSubmit = prompt.trim().length > 0 && hasCredits && !submitting;

  // ── Handlers ────────────────────────────────────────────────────────────────

  async function handleSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const session = await api.createSession({
        prompt: `[lang:${language}] ${prompt}`,
        repo_id: selectedRepo || undefined,
      });
      setSessionId(session.id);
      setPageState("streaming");
    } catch (err) {
      console.error("Failed to create session:", err);
    } finally {
      setSubmitting(false);
    }
  }

  function handleNewPrompt() {
    setPageState("input");
    setSessionId(null);
    setPrompt("");
    setLanguage("auto");
    setSelectedRepo(null);
    setAttachedFiles([]);
    setActiveTab("code");
    setActiveFileIndex(0);
    setExplanationText("");
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    setAttachedFiles((prev) => [...prev, ...files]);
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) {
      setAttachedFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
    }
  }

  function removeFile(index: number) {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleCopyCode() {
    const chunk = stream.codeChunks[activeFileIndex];
    if (!chunk) return;
    await navigator.clipboard.writeText(chunk.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleDownload() {
    const chunk = stream.codeChunks[activeFileIndex];
    if (!chunk) return;
    const blob = new Blob([chunk.content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = chunk.file || "codey-output.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleDownloadAll() {
    stream.codeChunks.forEach((chunk) => {
      const blob = new Blob([chunk.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = chunk.file || "codey-output.txt";
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  // ── Monaco language mapping ─────────────────────────────────────────────────

  function monacoLang(chunk: CodeChunk): string {
    const ext = chunk.file?.split(".").pop()?.toLowerCase();
    const map: Record<string, string> = {
      py: "python",
      js: "javascript",
      jsx: "javascript",
      ts: "typescript",
      tsx: "typescript",
      java: "java",
      go: "go",
      rs: "rust",
      rb: "ruby",
      php: "php",
      css: "css",
      html: "html",
      json: "json",
      yaml: "yaml",
      yml: "yaml",
      md: "markdown",
      sql: "sql",
      sh: "shell",
      bash: "shell",
    };
    return map[ext || ""] || chunk.language || "plaintext";
  }

  // ── INPUT STATE ─────────────────────────────────────────────────────────────

  if (pageState === "input") {
    return (
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">New Prompt</h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Describe what you want Codey to build, fix, or refactor.
          </p>
        </div>

        {/* ── Prompt Textarea ──────────────────────────────────────────── */}
        <div className="rounded-xl border border-codey-border bg-codey-card">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={`Describe what you want Codey to build or fix...\n\nExamples:\n- "Build a REST API for user authentication with JWT tokens in Python"\n- "Refactor this React component to use hooks instead of class state"\n- "Add input validation and error handling to the checkout flow"\n- "Write unit tests for the PaymentService class"`}
            rows={10}
            className="w-full resize-none rounded-t-xl border-none bg-transparent px-5 py-4 text-sm text-codey-text placeholder:text-codey-text-muted focus:outline-none focus:ring-0"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                handleSubmit();
              }
            }}
          />

          {/* ── Options Row ────────────────────────────────────────────── */}
          <div className="flex flex-wrap items-center gap-3 border-t border-codey-border/50 px-5 py-3">
            {/* Language selector */}
            <div className="relative">
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="appearance-none rounded-lg border border-codey-border bg-codey-bg py-2 pl-3 pr-8 text-xs text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
              >
                {LANGUAGES.map((lang) => (
                  <option key={lang.value} value={lang.value}>
                    {lang.label}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-codey-text-muted" />
            </div>

            {/* Repo selector */}
            {repos.length > 0 && (
              <div className="relative">
                <select
                  value={selectedRepo || ""}
                  onChange={(e) => setSelectedRepo(e.target.value || null)}
                  className="appearance-none rounded-lg border border-codey-border bg-codey-bg py-2 pl-3 pr-8 text-xs text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                >
                  <option value="">No repo context</option>
                  {repos.map((repo) => (
                    <option key={repo.id} value={repo.id}>
                      {repo.name}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-codey-text-muted" />
              </div>
            )}

            <div className="flex-1" />

            {/* Credit estimate */}
            {prompt.length > 0 && (
              <div className="flex items-center gap-1.5 text-xs text-codey-text-dim">
                <Zap className="h-3 w-3 text-codey-green" />
                Estimated: ~{estimatedCredits} credit{estimatedCredits !== 1 ? "s" : ""}
              </div>
            )}
          </div>
        </div>

        {/* ── File Attachment Zone ────────────────────────────────────── */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed p-6 text-center transition-all ${
            dragOver
              ? "border-codey-green bg-codey-green/5"
              : "border-codey-border hover:border-codey-border-light hover:bg-codey-card/50"
          }`}
        >
          <Upload
            className={`mx-auto h-6 w-6 ${
              dragOver ? "text-codey-green" : "text-codey-text-muted"
            }`}
          />
          <p className="mt-2 text-sm text-codey-text-dim">
            Drag & drop files here, or <span className="text-codey-green">browse</span>
          </p>
          <p className="mt-1 text-xs text-codey-text-muted">
            .py, .js, .ts, .java, .go, .rs, .zip
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".py,.js,.jsx,.ts,.tsx,.java,.go,.rs,.zip,.json,.yaml,.yml,.md,.txt,.css,.html,.sql,.sh"
            onChange={handleFileSelect}
            className="hidden"
          />
        </div>

        {/* ── Attached Files ──────────────────────────────────────────── */}
        {attachedFiles.length > 0 && (
          <div className="space-y-2">
            {attachedFiles.map((file, i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-card px-4 py-2"
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
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* ── Submit ──────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between">
          {!hasCredits && (
            <p className="text-sm text-codey-red">
              No credits remaining.{" "}
              <a href="/credits" className="underline">
                Top up
              </a>{" "}
              to continue.
            </p>
          )}
          <div className="flex-1" />
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-2 rounded-xl bg-codey-green px-8 py-3 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting...
              </>
            ) : (
              <>
                Generate with Codey
                <Zap className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </div>
    );
  }

  // ── STREAMING STATE ─────────────────────────────────────────────────────────

  const currentChunk = stream.codeChunks[activeFileIndex];
  const allCode = stream.codeChunks.map((c) => c.content).join("\n\n");

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      {/* ── Progress Indicator ──────────────────────────────────────── */}
      <div className="rounded-xl border border-codey-border bg-codey-card p-4">
        <div className="flex items-center gap-3">
          {!stream.isComplete ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin text-codey-green" />
              <span className="text-sm font-medium text-codey-text">
                {stream.status || "Connecting..."}
              </span>
            </>
          ) : (
            <>
              <Check className="h-4 w-4 text-codey-green" />
              <span className="text-sm font-medium text-codey-green">
                Generation complete
              </span>
            </>
          )}

          {stream.error && (
            <span className="ml-auto text-xs text-codey-red">{stream.error}</span>
          )}
        </div>

        {/* Plan steps */}
        {stream.plan && (
          <div className="mt-3 space-y-1.5">
            {stream.plan.steps.map((step) => (
              <div key={step.id} className="flex items-center gap-2 text-xs">
                {step.status === "done" && (
                  <Check className="h-3 w-3 text-codey-green" />
                )}
                {step.status === "running" && (
                  <Loader2 className="h-3 w-3 animate-spin text-codey-yellow" />
                )}
                {step.status === "pending" && (
                  <div className="h-3 w-3 rounded-full border border-codey-border" />
                )}
                {step.status === "failed" && (
                  <X className="h-3 w-3 text-codey-red" />
                )}
                <span
                  className={
                    step.status === "done"
                      ? "text-codey-text-dim"
                      : step.status === "running"
                        ? "text-codey-text"
                        : "text-codey-text-muted"
                  }
                >
                  {step.description}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Tabs ────────────────────────────────────────────────────── */}
      <div className="flex gap-1 rounded-xl border border-codey-border bg-codey-card p-1">
        {(
          [
            { id: "code", label: "Code", icon: FileCode },
            { id: "explanation", label: "Explanation", icon: BookOpen },
            { id: "health", label: "Structural Impact", icon: Activity },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? "bg-codey-green/10 text-codey-green"
                : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
            }`}
          >
            <tab.icon className="h-4 w-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Code Tab ───────────────────────────────────────────────── */}
      {activeTab === "code" && (
        <div className="rounded-xl border border-codey-border bg-codey-card">
          {/* File tabs */}
          {stream.codeChunks.length > 1 && (
            <div className="flex gap-1 overflow-x-auto border-b border-codey-border px-3 pt-3">
              {stream.codeChunks.map((chunk, i) => (
                <button
                  key={i}
                  onClick={() => setActiveFileIndex(i)}
                  className={`shrink-0 rounded-t-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    activeFileIndex === i
                      ? "bg-codey-bg text-codey-green"
                      : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                  }`}
                >
                  {chunk.file || `File ${i + 1}`}
                </button>
              ))}
            </div>
          )}

          {/* Monaco editor */}
          {stream.codeChunks.length > 0 ? (
            <MonacoEditor
              height="480px"
              language={currentChunk ? monacoLang(currentChunk) : "plaintext"}
              value={currentChunk?.content || ""}
              theme="vs-dark"
              options={{
                readOnly: true,
                minimap: { enabled: false },
                fontSize: 13,
                fontFamily: "JetBrains Mono, Fira Code, monospace",
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                padding: { top: 16, bottom: 16 },
                renderLineHighlight: "none",
                wordWrap: "on",
                overviewRulerLanes: 0,
              }}
            />
          ) : (
            <div className="flex h-96 items-center justify-center text-sm text-codey-text-dim">
              {stream.isComplete
                ? "No code output generated"
                : "Waiting for code output..."}
            </div>
          )}
        </div>
      )}

      {/* ── Explanation Tab ─────────────────────────────────────────── */}
      {activeTab === "explanation" && (
        <div className="rounded-xl border border-codey-border bg-codey-card p-6">
          {explanationText ? (
            <RenderMarkdown text={explanationText} />
          ) : (
            <div className="flex h-48 items-center justify-center text-sm text-codey-text-dim">
              {stream.isComplete
                ? "No explanation provided"
                : "Waiting for explanation..."}
            </div>
          )}
        </div>
      )}

      {/* ── Structural Impact Tab ────────────────────────────────────── */}
      {activeTab === "health" && (
        <div className="space-y-4">
          {stream.healthBefore || stream.healthAfter ? (
            <>
              {/* Before/After Grade Cards */}
              <div className="grid gap-4 sm:grid-cols-2">
                {stream.healthBefore && (
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                      Before
                    </p>
                    <HealthGrade report={stream.healthBefore} />
                  </div>
                )}
                {stream.healthAfter && (
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                      After
                    </p>
                    <HealthGrade report={stream.healthAfter} />
                  </div>
                )}
              </div>

              {/* Metric deltas */}
              {stream.healthBefore && stream.healthAfter && (
                <div className="rounded-xl border border-codey-border bg-codey-card p-5">
                  <h3 className="mb-3 text-sm font-semibold text-codey-text">
                    Metric Deltas
                  </h3>
                  <div className="space-y-2">
                    <HealthDelta
                      label="Overall Score"
                      before={stream.healthBefore.score}
                      after={stream.healthAfter.score}
                    />
                    {Object.keys(stream.healthAfter.breakdown).map((key) => (
                      <HealthDelta
                        key={key}
                        label={key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                        before={stream.healthBefore?.breakdown[key]}
                        after={stream.healthAfter?.breakdown[key]}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Affected components */}
              {stream.healthAfter?.breakdown && (
                <div className="rounded-xl border border-codey-border bg-codey-card p-5">
                  <h3 className="mb-3 text-sm font-semibold text-codey-text">
                    Affected Components
                  </h3>
                  <div className="space-y-1.5">
                    {stream.codeChunks.map((chunk, i) => {
                      const improved =
                        stream.healthAfter &&
                        stream.healthBefore &&
                        stream.healthAfter.score > stream.healthBefore.score;
                      return (
                        <div
                          key={i}
                          className="flex items-center gap-2 rounded-lg bg-codey-bg px-3 py-2 text-xs"
                        >
                          <div
                            className={`h-2 w-2 rounded-full ${
                              improved ? "bg-codey-green" : "bg-codey-red"
                            }`}
                          />
                          <span className="font-mono text-codey-text-dim">
                            {chunk.file || `Component ${i + 1}`}
                          </span>
                          <span className="text-codey-text-muted">
                            ({chunk.action})
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="rounded-xl border border-codey-border bg-codey-card p-6">
              <div className="flex h-48 items-center justify-center text-sm text-codey-text-dim">
                {stream.isComplete
                  ? "No health data available for this session"
                  : "Waiting for structural analysis..."}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Action Buttons (after completion) ──────────────────────── */}
      {stream.isComplete && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-codey-border bg-codey-card px-5 py-4">
          <button
            onClick={handleCopyCode}
            className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            {copied ? (
              <>
                <Check className="h-4 w-4 text-codey-green" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" />
                Copy code
              </>
            )}
          </button>

          <button
            onClick={stream.codeChunks.length > 1 ? handleDownloadAll : handleDownload}
            className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            <Download className="h-4 w-4" />
            Download{stream.codeChunks.length > 1 ? " all" : ""}
          </button>

          {selectedRepo && (
            <button className="flex items-center gap-2 rounded-lg border border-codey-green/30 bg-codey-green/10 px-4 py-2 text-sm font-medium text-codey-green transition-colors hover:bg-codey-green/20">
              <GitCommit className="h-4 w-4" />
              Commit to GitHub
            </button>
          )}

          <div className="flex-1" />

          <button
            onClick={handleNewPrompt}
            className="flex items-center gap-2 rounded-lg bg-codey-green px-5 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green"
          >
            <RotateCcw className="h-4 w-4" />
            New prompt
          </button>
        </div>
      )}

      {/* ── Credits charged ────────────────────────────────────────── */}
      {stream.isComplete && (
        <div className="text-center text-xs text-codey-text-muted">
          Session used {estimatedCredits} credit{estimatedCredits !== 1 ? "s" : ""}
        </div>
      )}
    </div>
  );
}
