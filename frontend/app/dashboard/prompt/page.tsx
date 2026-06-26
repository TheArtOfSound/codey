"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { useAuth } from "@/lib/auth";
import { api, type PromptHealthReport, type Repo, type Session } from "@/lib/api";
import { useToast } from "@/components/ui/ToastProvider";
import { repoMissionTemplates } from "@/lib/repo-work";
import { type CodeChunk, type HealthReport, type SessionStreamState } from "@/lib/websocket";
import {
  Zap,
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
  AlertTriangle,
  Clock3,
  History,
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

type PageState = "input" | "working" | "result";
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

const TEXT_ATTACHMENT_EXTENSIONS = new Set([
  "py",
  "js",
  "jsx",
  "ts",
  "tsx",
  "java",
  "go",
  "rs",
  "json",
  "yaml",
  "yml",
  "md",
  "txt",
  "css",
  "html",
  "sql",
  "sh",
  "bash",
  "toml",
]);

function estimateCredits(promptLength: number): number {
  if (promptLength === 0) return 0;
  return Math.max(1, Math.ceil(promptLength / 200) + 1);
}

function gradeForScore(score: number): string {
  if (score >= 0.85) return "A";
  if (score >= 0.7) return "B";
  if (score >= 0.55) return "C";
  return "D";
}

function mapPromptHealth(health: PromptHealthReport | null): HealthReport | null {
  if (!health) return null;
  return {
    score: health.health_score,
    grade: gradeForScore(health.health_score),
    breakdown: {
      coherence: health.coherence,
      stability: health.stability,
    },
  };
}

function mapStoredHealth(score: number | null): HealthReport | null {
  if (score === null) return null;
  return {
    score,
    grade: gradeForScore(score),
    breakdown: {},
  };
}

function stripPromptMetadata(value: string): string {
  return value.replace(/^\[lang:[^\]]+\]\s*/, "").trim();
}

function extractPromptLanguage(value: string): string {
  const match = value.match(/^\[lang:([^\]]+)\]/);
  return match?.[1] || "auto";
}

function defaultExtension(languageHint: string): string {
  const map: Record<string, string> = {
    python: ".py",
    javascript: ".js",
    typescript: ".ts",
    java: ".java",
    go: ".go",
    rust: ".rs",
    html: ".html",
    css: ".css",
    json: ".json",
    sql: ".sql",
    shell: ".sh",
    bash: ".sh",
  };
  return map[languageHint.toLowerCase()] || ".txt";
}

function defaultFilename(languageHint: string): string {
  return `generated${defaultExtension(languageHint)}`;
}

function languageFromFilename(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase();
  const map: Record<string, string> = {
    py: "python",
    js: "javascript",
    jsx: "javascript",
    ts: "typescript",
    tsx: "typescript",
    java: "java",
    go: "go",
    rs: "rust",
    html: "html",
    css: "css",
    json: "json",
    sql: "sql",
    sh: "shell",
  };
  return map[ext || ""] || "plaintext";
}

function monacoLang(chunk: CodeChunk): string {
  return languageFromFilename(chunk.file || "") || chunk.language || "plaintext";
}

function parseJsonPayload(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  const candidates = [trimmed];

  const fencedMatch = trimmed.match(/^```(?:json)?\s*\n([\s\S]*?)```$/);
  if (fencedMatch?.[1]) {
    candidates.unshift(fencedMatch[1].trim());
  }

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      continue;
    }
  }

  return null;
}

function parseCodeChunks(output: string | null, requestedLanguage: string): CodeChunk[] {
  if (!output) return [];

  const matches = [...output.matchAll(/```([^\n`]*)\n([\s\S]*?)```/g)];
  if (matches.length === 0) {
    return [
      {
        file: defaultFilename(requestedLanguage === "auto" ? "plaintext" : requestedLanguage),
        language: requestedLanguage === "auto" ? "plaintext" : requestedLanguage,
        content: output.trim(),
        action: "create",
      },
    ];
  }

  return matches
    .map<CodeChunk | null>((match, index) => {
      const header = match[1].trim();
      const content = match[2].trim();
      if (!content) return null;

      const explicitFile = /[./\\]/.test(header);
      const file = explicitFile
        ? header
        : index === 0
          ? defaultFilename(header || requestedLanguage)
          : `generated-${index + 1}${defaultExtension(header || requestedLanguage)}`;
      const language = explicitFile
        ? languageFromFilename(header)
        : header || (requestedLanguage === "auto" ? "plaintext" : requestedLanguage);

      return {
        file,
        language,
        content,
        action: "create" as const,
      };
    })
    .filter((chunk): chunk is CodeChunk => chunk !== null);
}

function stripCodeBlocks(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "").trim();
}

function buildArtifacts(
  output: string | null,
  requestedLanguage: string,
  health: PromptHealthReport | null,
  securityIssues: string[] = []
): { codeChunks: CodeChunk[]; explanation: string } {
  const rawOutput = output?.trim() || "";
  if (!rawOutput) {
    return {
      codeChunks: [],
      explanation: health?.summary || "",
    };
  }

  let codeSource = rawOutput;
  let explanation = "";

  const structured = parseJsonPayload(rawOutput);
  if (structured) {
    if (typeof structured.code === "string" && structured.code.trim()) {
      codeSource = structured.code.trim();
    }
    if (typeof structured.explanation === "string" && structured.explanation.trim()) {
      explanation = structured.explanation.trim();
    }
  }

  const codeChunks = parseCodeChunks(codeSource, requestedLanguage);

  if (!explanation) {
    const remainder = stripCodeBlocks(codeSource);
    if (remainder && remainder !== codeSource) {
      explanation = remainder;
    }
  }

  if (!explanation && health?.summary) {
    explanation = health.summary;
  }

  if (securityIssues.length > 0) {
    const securityText = `Security notes:\n${securityIssues.map((issue) => `- ${issue}`).join("\n")}`;
    explanation = explanation ? `${explanation}\n\n${securityText}` : securityText;
  }

  if (!explanation && codeChunks.length === 0) {
    explanation = rawOutput;
  }

  return { codeChunks, explanation };
}

function buildResultState(params: {
  status: string;
  codeChunks: CodeChunk[];
  healthAfter: HealthReport | null;
  error: string | null;
}): SessionStreamState {
  return {
    messages: [],
    status: params.status,
    connected: false,
    codeChunks: params.codeChunks,
    healthBefore: null,
    healthAfter: params.healthAfter,
    plan: null,
    isComplete: params.status === "completed" || params.status === "failed",
    error: params.error,
  };
}

function buildFixPrompt(issue: string, filePath?: string | null): string {
  const fileContext = filePath ? ` in \`${filePath}\`` : "";
  return `Fix the structural issue around "${issue}"${fileContext}. Return the updated code${filePath ? ` for ${filePath}` : ""}, explain what changed, and preserve existing behavior outside the affected area.`;
}

function formatApiError(error: unknown): string {
  if (!error || typeof error !== "object") {
    return "Something went wrong. Please try again.";
  }

  const maybeDetail = (error as { detail?: unknown }).detail;
  if (typeof maybeDetail === "string") {
    return maybeDetail;
  }

  if (maybeDetail && typeof maybeDetail === "object") {
    const message = (maybeDetail as { message?: unknown }).message;
    if (typeof message === "string") {
      return message;
    }
  }

  return "Something went wrong. Please try again.";
}

function RenderMarkdown({ text }: { text: string }) {
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
        if (line.trim() === "") {
          return <div key={i} className="h-2" />;
        }
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
        <p className="text-xs text-codey-text-dim">Structural Health Grade</p>
      </div>
    </div>
  );
}

function isTextAttachment(file: File): boolean {
  const ext = file.name.split(".").pop()?.toLowerCase();
  return !!ext && TEXT_ATTACHMENT_EXTENSIONS.has(ext);
}

async function buildAttachmentBlock(file: File): Promise<string> {
  if (!isTextAttachment(file)) {
    return `File: ${file.name}\n(Binary attachment provided. Use filename and surrounding prompt context; content was not inlined.)`;
  }

  const text = await file.text();
  return `File: ${file.name}\n\`\`\`\n${text.slice(0, 12000)}\n\`\`\``;
}

export default function PromptPage() {
  const { user } = useAuth();
  const searchParams = useSearchParams();

  const fixParam = searchParams.get("fix");
  const fileParam = searchParams.get("file");
  const repoParam = searchParams.get("repo");
  const sessionParam = searchParams.get("session");
  const initialPrefillPrompt = fixParam ? buildFixPrompt(fixParam, fileParam) : "";
  const initialFixKey = fixParam ? `${fixParam}:${fileParam || ""}` : null;

  const [prompt, setPrompt] = useState(initialPrefillPrompt);
  const [language, setLanguage] = useState("auto");
  const [selectedRepo, setSelectedRepo] = useState<string | null>(repoParam);
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [pageState, setPageState] = useState<PageState>("input");
  const [generatedSessionId, setGeneratedSessionId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<StreamTab>("code");
  const [activeFileIndex, setActiveFileIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const [generatedStream, setGeneratedStream] = useState<SessionStreamState | null>(null);
  const [explanationText, setExplanationText] = useState("");
  const [commitLoading, setCommitLoading] = useState(false);
  const [commitMessage, setCommitMessage] = useState<string | null>(null);
  const [commitResult, setCommitResult] = useState<{ pull_request_url?: string | null; branch?: string | null; files_changed?: string[]; verified?: boolean; pr_error?: string | null } | null>(null);
  const { addToast } = useToast();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [sessionRecord, setSessionRecord] = useState<Session | null>(null);
  const [sessionSourceLabel, setSessionSourceLabel] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const promptInputRef = useRef<HTMLTextAreaElement>(null);
  const restoredSessionRef = useRef<string | null>(null);
  const appliedFixRef = useRef<string | null>(initialFixKey);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);

  useEffect(() => {
    api.getRepos().then(setRepos).catch(() => {});
  }, []);

  useEffect(() => {
    if (repoParam && repoParam !== selectedRepo) {
      setSelectedRepo(repoParam);
    }
  }, [repoParam, selectedRepo]);

  useEffect(() => {
    const fixKey = fixParam ? `${fixParam}:${fileParam || ""}` : null;
    if (!fixParam || !fixKey || appliedFixRef.current === fixKey) return;

    setPrompt(buildFixPrompt(fixParam, fileParam));
    setPageState("input");
    setGeneratedSessionId(null);
    setGeneratedStream(null);
    setSessionRecord(null);
    setSessionSourceLabel(null);
    setExplanationText("");
    setActiveTab("code");
    setActiveFileIndex(0);
    setCommitMessage(null); setCommitResult(null);
    setSubmitError(null);
    setSelectedTemplateId(null);
    appliedFixRef.current = fixKey;
  }, [fixParam, fileParam]);

  useEffect(() => {
    if (!sessionParam || restoredSessionRef.current === sessionParam) return;

    restoredSessionRef.current = sessionParam;
    setLoadingSession(true);
    setSubmitError(null);

    void api
      .getSession(sessionParam)
      .then((session) => {
        const restoredLanguage = extractPromptLanguage(session.prompt || "");
        const artifacts = buildArtifacts(session.result_summary, restoredLanguage, null);

        setPrompt(stripPromptMetadata(session.prompt || ""));
        setLanguage(restoredLanguage);
        setSelectedRepo(session.repo_id);
        setGeneratedSessionId(session.id);
        setSessionRecord(session);
        setSessionSourceLabel("Restored from session history");
        setGeneratedStream(
          buildResultState({
            status: session.status,
            codeChunks: artifacts.codeChunks,
            healthAfter: mapStoredHealth(session.health_score_after),
            error: session.error_message,
          })
        );
        setExplanationText(
          artifacts.explanation ||
            (session.error_message ? `Session failed: ${session.error_message}` : "")
        );
        setSelectedTemplateId(null);
        setActiveTab(artifacts.codeChunks.length > 0 ? "code" : "explanation");
        setActiveFileIndex(0);
        setPageState("result");
      })
      .catch((error) => {
        setSubmitError(formatApiError(error));
        restoredSessionRef.current = null;
      })
      .finally(() => {
        setLoadingSession(false);
      });
  }, [sessionParam]);

  const estimatedCredits = estimateCredits(prompt.length);
  const hasCredits = (user?.credits_remaining ?? 0) > 0;
  const canSubmit = prompt.trim().length > 0 && hasCredits && !submitting && !loadingSession;
  const displayStream = generatedStream;
  const currentChunk = displayStream?.codeChunks[activeFileIndex] || null;
  const actualCredits = sessionRecord?.credits_used ?? estimatedCredits;
  const selectedRepoName = repos.find((repo) => repo.id === selectedRepo)?.name || null;

  async function handleSubmit() {
    if (!canSubmit) return;

    setSubmitting(true);
    setPageState("working");
    setSubmitError(null);
    setCommitMessage(null); setCommitResult(null);
    setGeneratedStream(null);
    setSessionRecord(null);

    try {
      const attachmentBlocks = await Promise.all(
        attachedFiles.slice(0, 5).map((file) => buildAttachmentBlock(file))
      );

      const composedPrompt = [
        `[lang:${language}] ${prompt}`,
        attachmentBlocks.length > 0
          ? `Attached files for context:\n\n${attachmentBlocks.join("\n\n")}`
          : "",
      ]
        .filter(Boolean)
        .join("\n\n");

      const result = await api.generateCode({
        prompt: composedPrompt,
        repo_id: selectedRepo || undefined,
        language: language === "auto" ? undefined : language,
      });

      const persistedSession = await api.getSession(result.session_id).catch(() => null);
      const artifacts = buildArtifacts(
        result.output,
        language,
        result.health,
        result.security_issues
      );

      setGeneratedSessionId(result.session_id);
      setSessionRecord(persistedSession);
      setSessionSourceLabel(
        selectedRepoName
          ? `Prepared against ${selectedRepoName}`
          : "Prepared without repo context"
      );
      setGeneratedStream(
        buildResultState({
          status: result.status,
          codeChunks: artifacts.codeChunks,
          healthAfter: mapPromptHealth(result.health) || mapStoredHealth(persistedSession?.health_score_after ?? null),
          error: persistedSession?.error_message || null,
        })
      );
      setExplanationText(artifacts.explanation);
      setActiveTab(artifacts.codeChunks.length > 0 ? "code" : "explanation");
      setActiveFileIndex(0);
      setPageState("result");
    } catch (error) {
      setSubmitError(formatApiError(error));
      setPageState("input");
    } finally {
      setSubmitting(false);
    }
  }

  function handleNewPrompt() {
    setPageState("input");
    setGeneratedSessionId(null);
    setGeneratedStream(null);
    setPrompt("");
    setAttachedFiles([]);
    setActiveTab("code");
    setActiveFileIndex(0);
    setExplanationText("");
    setCommitMessage(null); setCommitResult(null);
    setSubmitError(null);
    setSessionRecord(null);
    setSessionSourceLabel(null);
    setSelectedTemplateId(null);
  }

  function applyTemplate(templateId: string, templatePrompt: string) {
    setSelectedTemplateId(templateId);
    setPrompt(templatePrompt);
    setSubmitError(null);
    setCommitMessage(null); setCommitResult(null);
    setPageState("input");
    requestAnimationFrame(() => {
      promptInputRef.current?.focus();
      promptInputRef.current?.setSelectionRange(
        promptInputRef.current.value.length,
        promptInputRef.current.value.length
      );
    });
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
    if (!currentChunk) return;
    await navigator.clipboard.writeText(currentChunk.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function downloadFile(chunk: CodeChunk) {
    const blob = new Blob([chunk.content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = chunk.file || "codey-output.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleDownload() {
    if (!currentChunk) return;
    downloadFile(currentChunk);
  }

  function handleDownloadAll() {
    displayStream?.codeChunks.forEach(downloadFile);
  }

  async function handleCommitToGitHub() {
    if (!generatedSessionId || !selectedRepo) return;
    setCommitLoading(true);
    setCommitMessage(null); setCommitResult(null);
    try {
      const result = await api.commitSession(generatedSessionId);
      setCommitMessage(result.message);
      setCommitResult(result);
      addToast(result.pull_request_url ? "Pull request opened \u2713" : result.message,
               result.pull_request_url ? "success" : "info");
    } catch (error) {
      setCommitMessage(formatApiError(error));
      addToast(formatApiError(error), "error");
    } finally {
      setCommitLoading(false);
    }
  }

  if (pageState === "working") {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">Preparing Intervention</h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Codey is packaging a repository run from this brief. This path is currently request-response, not the full streamed operator workspace.
          </p>
        </div>

        <div className="rounded-xl border border-codey-border bg-codey-card p-6">
          <div className="flex items-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-codey-green" />
            <div>
              <p className="text-sm font-medium text-codey-text">Building intervention package</p>
              <p className="text-xs text-codey-text-dim">
                Repo context: {selectedRepoName || "none"} · Attachments: {attachedFiles.length}
              </p>
            </div>
          </div>
          <div className="mt-4 rounded-lg border border-codey-border bg-codey-bg p-4 text-sm text-codey-text-dim">
            <p className="font-medium text-codey-text">Intervention brief</p>
            <p className="mt-2 whitespace-pre-wrap">{prompt}</p>
          </div>
        </div>
      </div>
    );
  }

  if (pageState === "input") {
    return (
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">Intervention Console</h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Queue repo maintenance runs with the same operator lanes Codey uses for scan, repair, CI, security, docs, and ship work.
          </p>
        </div>

        {(submitError || loadingSession || fixParam || sessionParam) && (
          <div className="rounded-xl border border-codey-border bg-codey-card p-4">
            {loadingSession && (
              <div className="flex items-center gap-2 text-sm text-codey-text">
                <Loader2 className="h-4 w-4 animate-spin text-codey-green" />
                Restoring session workspace...
              </div>
            )}
            {!loadingSession && fixParam && (
              <div className="flex items-start gap-2 text-sm text-codey-text-dim">
                <AlertTriangle className="mt-0.5 h-4 w-4 text-codey-yellow" />
                <div>
                  <p className="font-medium text-codey-text">Prefilled from repo scan</p>
                  <p className="mt-1">
                    Focus area: <span className="text-codey-text">{fixParam}</span>
                    {fileParam ? (
                      <>
                        {" "}
                        in <code className="rounded bg-codey-bg px-1.5 py-0.5 text-xs">{fileParam}</code>
                      </>
                    ) : null}
                  </p>
                </div>
              </div>
            )}
            {!loadingSession && sessionParam && !submitError && (
              <div className="flex items-center gap-2 text-sm text-codey-text-dim">
                <History className="h-4 w-4 text-codey-green" />
                Reopening saved session <code className="rounded bg-codey-bg px-1.5 py-0.5 text-xs">{sessionParam}</code>
              </div>
            )}
            {!loadingSession && submitError && (
              <div className="flex items-start gap-2 text-sm text-codey-red">
                <AlertTriangle className="mt-0.5 h-4 w-4" />
                <span>{submitError}</span>
              </div>
            )}
          </div>
        )}

        <div className="rounded-xl border border-codey-border bg-codey-card">
          <div className="border-b border-codey-border px-5 py-4">
            <h2 className="text-sm font-semibold text-codey-text">Mission Templates</h2>
            <p className="mt-1 text-xs text-codey-text-dim">
              These templates mirror Codey&apos;s repo-work coverage 1:1. Start from one, then tighten the brief around your repo.
            </p>
          </div>
          <div className="grid gap-0 md:grid-cols-2">
            {repoMissionTemplates.map((template) => {
              const active = selectedTemplateId === template.id;
              return (
                <button
                  key={template.id}
                  onClick={() => applyTemplate(template.id, template.prompt)}
                  className={`border-b border-codey-border/50 px-5 py-4 text-left transition-colors md:border-r even:md:border-r-0 ${
                    active
                      ? "bg-codey-green/10"
                      : "hover:bg-codey-card-hover"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-codey-text">{template.label}</p>
                    <span className="rounded-full border border-codey-border bg-codey-bg px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-codey-text-muted">
                      {template.category}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-codey-text-dim">
                    {template.description}
                  </p>
                </button>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border border-codey-border bg-codey-card">
          <textarea
            ref={promptInputRef}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={`Describe the repo operation you want Codey to run...\n\nExamples:\n- "Patch the auth callback loop and keep the blast radius limited to the dashboard auth flow"\n- "Prepare a dependency update run for the billing service and flag any migration risk"\n- "Refactor duplicated state in this React component and summarize regression exposure"\n- "Write the updated file for api/auth.py, explain the failure mode, and keep notes PR-ready"`}
            rows={10}
            className="w-full resize-none rounded-t-xl border-none bg-transparent px-5 py-4 text-sm text-codey-text placeholder:text-codey-text-muted focus:outline-none focus:ring-0"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                void handleSubmit();
              }
            }}
          />

          <div className="flex flex-wrap items-center gap-3 border-t border-codey-border/50 px-5 py-3">
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

            {repos.length > 0 && (
              <div className="relative">
                <select
                  value={selectedRepo || ""}
                  onChange={(e) => setSelectedRepo(e.target.value || null)}
                  className="appearance-none rounded-lg border border-codey-border bg-codey-bg py-2 pl-3 pr-8 text-xs text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                >
                  <option value="">No repo selected</option>
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

            {prompt.length > 0 && (
              <div className="flex items-center gap-1.5 text-xs text-codey-text-dim">
                <Zap className="h-3 w-3 text-codey-green" />
                Estimated run cost: ~{estimatedCredits} credit{estimatedCredits !== 1 ? "s" : ""}
              </div>
            )}
          </div>
        </div>

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
            Text files are inlined for context. Binary files are referenced by name only.
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".py,.js,.jsx,.ts,.tsx,.java,.go,.rs,.zip,.json,.yaml,.yml,.md,.txt,.css,.html,.sql,.sh,.toml"
            onChange={handleFileSelect}
            className="hidden"
          />
        </div>

        {attachedFiles.length > 0 && (
          <div className="space-y-2">
            {attachedFiles.map((file, i) => (
              <div
                key={`${file.name}-${i}`}
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

        <div className="flex items-center justify-between">
          {!hasCredits && (
            <p className="text-sm text-codey-red">
              No credits remaining.{" "}
              <Link href="/dashboard/credits" className="underline">
                Top up
              </Link>{" "}
              to continue.
            </p>
          )}
          <div className="flex-1" />
          <button
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className="flex items-center gap-2 rounded-xl bg-codey-green px-8 py-3 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Queueing...
              </>
            ) : (
              <>
                Queue with Codey
                <Zap className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </div>
    );
  }

  if (!displayStream) return null;

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="rounded-xl border border-codey-border bg-codey-card p-4">
        <div className="flex flex-wrap items-center gap-3">
          {displayStream.error ? (
            <>
              <AlertTriangle className="h-4 w-4 text-codey-red" />
              <span className="text-sm font-medium text-codey-red">Session failed</span>
            </>
          ) : (
            <>
              <Check className="h-4 w-4 text-codey-green" />
              <span className="text-sm font-medium text-codey-green">Run complete</span>
            </>
          )}
          {sessionSourceLabel && (
            <span className="rounded-full border border-codey-border bg-codey-bg px-3 py-1 text-xs text-codey-text-dim">
              {sessionSourceLabel}
            </span>
          )}
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
            <p className="text-xs uppercase tracking-wider text-codey-text-muted">Session</p>
            <p className="mt-1 text-sm font-medium text-codey-text">
              {generatedSessionId?.slice(0, 8) || "n/a"}
            </p>
          </div>
          <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
            <p className="text-xs uppercase tracking-wider text-codey-text-muted">Repo Context</p>
            <p className="mt-1 text-sm font-medium text-codey-text">
              {selectedRepoName || "None"}
            </p>
          </div>
          <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
            <p className="text-xs uppercase tracking-wider text-codey-text-muted">Credits Charged</p>
            <p className="mt-1 text-sm font-medium text-codey-text">{actualCredits}</p>
          </div>
          <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
            <p className="text-xs uppercase tracking-wider text-codey-text-muted">Lines Generated</p>
            <p className="mt-1 text-sm font-medium text-codey-text">
              {sessionRecord?.lines_generated ?? currentChunk?.content.split("\n").length ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
            <p className="text-xs uppercase tracking-wider text-codey-text-muted">Files Produced</p>
            <p className="mt-1 text-sm font-medium text-codey-text">
              {sessionRecord?.files_modified ?? displayStream.codeChunks.length}
            </p>
          </div>
        </div>

        {displayStream.error && (
          <div className="mt-4 rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
            {displayStream.error}
          </div>
        )}
      </div>

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

      {activeTab === "code" && (
        <div className="rounded-xl border border-codey-border bg-codey-card">
          {displayStream.codeChunks.length > 1 && (
            <div className="flex gap-1 overflow-x-auto border-b border-codey-border px-3 pt-3">
              {displayStream.codeChunks.map((chunk, i) => (
                <button
                  key={`${chunk.file}-${i}`}
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

          {displayStream.codeChunks.length > 0 ? (
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
              No code output was captured for this session.
            </div>
          )}
        </div>
      )}

      {activeTab === "explanation" && (
        <div className="rounded-xl border border-codey-border bg-codey-card p-6">
          {explanationText ? (
            <RenderMarkdown text={explanationText} />
          ) : (
            <div className="flex h-48 items-center justify-center text-sm text-codey-text-dim">
              No explanation was captured for this session.
            </div>
          )}
        </div>
      )}

      {activeTab === "health" && (
        <div className="space-y-4">
          {displayStream.healthAfter ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                {sessionRecord?.health_score_before !== null && sessionRecord?.health_score_before !== undefined ? (
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                      Before
                    </p>
                    <HealthGrade report={mapStoredHealth(sessionRecord.health_score_before)!} />
                  </div>
                ) : null}
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                    After
                  </p>
                  <HealthGrade report={displayStream.healthAfter} />
                </div>
              </div>

              {sessionRecord?.health_score_before !== null && sessionRecord?.health_score_before !== undefined ? (
                <div className="rounded-xl border border-codey-border bg-codey-card p-5">
                  <h3 className="mb-3 text-sm font-semibold text-codey-text">Metric Deltas</h3>
                  <div className="space-y-2">
                    <HealthDelta
                      label="Overall Score"
                      before={sessionRecord.health_score_before ?? undefined}
                      after={displayStream.healthAfter.score}
                    />
                    {Object.entries(displayStream.healthAfter.breakdown).map(([key, value]) => (
                      <HealthDelta
                        key={key}
                        label={key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                        before={undefined}
                        after={value}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="rounded-xl border border-codey-border bg-codey-card p-6">
              <div className="flex h-48 items-center justify-center text-sm text-codey-text-dim">
                No structural data was available for this session.
              </div>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-codey-border bg-codey-card px-5 py-4">
        <button
          onClick={() => void handleCopyCode()}
          disabled={!currentChunk}
          className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text disabled:cursor-not-allowed disabled:opacity-50"
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
          onClick={displayStream.codeChunks.length > 1 ? handleDownloadAll : handleDownload}
          disabled={displayStream.codeChunks.length === 0}
          className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Download className="h-4 w-4" />
          Download{displayStream.codeChunks.length > 1 ? " all" : ""}
        </button>

        {selectedRepo && generatedSessionId && (
          <button
            onClick={() => void handleCommitToGitHub()}
            disabled={commitLoading}
            className="flex items-center gap-2 rounded-lg border border-codey-green/30 bg-codey-green/10 px-4 py-2 text-sm font-medium text-codey-green transition-colors hover:bg-codey-green/20 disabled:opacity-50"
          >
            {commitLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCommit className="h-4 w-4" />}
            Commit to GitHub
          </button>
        )}

        <Link
          href="/dashboard/sessions"
          className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
        >
          <History className="h-4 w-4" />
          Run history
        </Link>

        <div className="flex-1" />

        <button
          onClick={handleNewPrompt}
          className="flex items-center gap-2 rounded-lg bg-codey-green px-5 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green"
        >
          <RotateCcw className="h-4 w-4" />
          New run
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-center text-xs text-codey-text-muted">
        <span>Actual usage: {actualCredits} credit{actualCredits !== 1 ? "s" : ""}</span>
        {sessionRecord?.completed_at ? (
          <span className="flex items-center gap-1">
            <Clock3 className="h-3 w-3" />
            Completed {new Date(sessionRecord.completed_at).toLocaleString()}
          </span>
        ) : null}
      </div>

      {commitMessage && (
        <div className="rounded-lg border border-codey-border bg-codey-card px-4 py-3 text-sm text-codey-text space-y-2">
          <div>{commitMessage}</div>
          {commitResult?.pull_request_url ? (
            <a
              href={commitResult.pull_request_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-md bg-codey-green px-3 py-1.5 font-medium text-black hover:opacity-90"
            >
              <GitCommit className="h-4 w-4" /> View pull request &rarr;
            </a>
          ) : null}
          {commitResult?.files_changed && commitResult.files_changed.length > 0 ? (
            <div className="text-xs text-codey-text-dim">
              <span className="font-medium text-codey-text">Files changed:</span>{" "}
              {commitResult.files_changed.join(", ")}
            </div>
          ) : null}
          {commitResult ? (
            <div className="text-xs text-codey-text-dim">
              {commitResult.verified
                ? "\u2713 Claims auto-verified against the diff"
                : "Auto-verification advisory \u2014 review the diff before merging"}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
