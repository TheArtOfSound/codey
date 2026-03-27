"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  Rocket,
  Server,
  Layout,
  Terminal,
  MessageCircle,
  Database,
  Smartphone,
  ShoppingCart,
  Zap,
  Send,
  Check,
  CheckCircle2,
  X,
  ChevronRight,
  ChevronDown,
  Folder,
  FileCode,
  FileText,
  Loader2,
  AlertTriangle,
  Download,
  Github,
  ArrowRight,
  RotateCcw,
  Gauge,
  Activity,
  Shield,
  Play,
  Eye,
  Pencil,
  Package,
  Layers,
  Code2,
  Cpu,
  Wifi,
  WifiOff,
} from "lucide-react";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center rounded-lg border border-codey-border bg-codey-bg">
      <div className="flex items-center gap-2 text-sm text-codey-text-dim">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading editor...
      </div>
    </div>
  ),
});

// ── Types ─────────────────────────────────────────────────────────────────────

type BuildState =
  | "DESCRIBE"
  | "CLARIFY"
  | "PLAN"
  | "BUILDING"
  | "CHECKPOINT"
  | "COMPLETE";

interface ClarificationQuestion {
  id: string;
  question: string;
  default: string | null;
  options: string[] | null;
}

interface TemplateMatch {
  template_id: string;
  name: string;
  confidence: number;
  estimated_credits: number;
}

interface PlanPhase {
  phase: number;
  name: string;
  files: string[];
  description: string;
}

interface FileTreeNode {
  name: string;
  type: "file" | "directory";
  children?: FileTreeNode[];
  language?: string;
}

interface BuildPlan {
  project_id: string;
  name: string;
  stack: Record<string, unknown>;
  file_tree: FileTreeNode[];
  phases: PlanPhase[];
  total_files: number;
  estimated_credits: number;
  estimated_lines: number;
}

interface BuildFileStatus {
  id: string;
  file_path: string;
  line_count: number | null;
  phase: number | null;
  status: "pending" | "generating" | "completed" | "failed";
  stress_score: number | null;
  validation_passed: boolean | null;
}

interface NfetHealth {
  es_score: number;
  kappa: number;
  sigma: number;
  phase: string;
}

interface BuildStreamMessage {
  type:
    | "status"
    | "phase"
    | "file_start"
    | "file_chunk"
    | "file_complete"
    | "checkpoint"
    | "nfet"
    | "error"
    | "complete"
    | "pong";
  data: Record<string, unknown>;
  timestamp: string;
}

interface CheckpointData {
  phase: number;
  phase_name: string;
  files_built: number;
  lines_generated: number;
  tests_passed: number;
  tests_failed: number;
  test_details: Array<{ name: string; passed: boolean; detail?: string }>;
  nfet: NfetHealth | null;
}

interface CompletionStats {
  total_files: number;
  total_lines: number;
  languages: string[];
  test_coverage: number;
  nfet_grade: string;
  nfet_es_score: number;
  credits_charged: number;
}

// ── Template data ─────────────────────────────────────────────────────────────

const TEMPLATE_ICONS: Record<string, React.ElementType> = {
  rocket: Rocket,
  server: Server,
  layout: Layout,
  terminal: Terminal,
  "message-circle": MessageCircle,
  database: Database,
  smartphone: Smartphone,
  "shopping-cart": ShoppingCart,
};

interface TemplateCard {
  id: string;
  name: string;
  description: string;
  icon: string;
  estimated_credits: number;
  languages: string[];
}

const TEMPLATES: TemplateCard[] = [
  {
    id: "saas-starter",
    name: "SaaS Starter",
    description: "Full-stack SaaS with auth, billing, dashboard",
    icon: "rocket",
    estimated_credits: 25,
    languages: ["TypeScript", "Python"],
  },
  {
    id: "rest-api",
    name: "REST API",
    description: "Production REST API with auth and docs",
    icon: "server",
    estimated_credits: 15,
    languages: ["Python", "SQL"],
  },
  {
    id: "react-app",
    name: "React App",
    description: "Modern React with routing and state",
    icon: "layout",
    estimated_credits: 18,
    languages: ["TypeScript", "CSS"],
  },
  {
    id: "cli-tool",
    name: "CLI Tool",
    description: "Command-line app with arg parsing",
    icon: "terminal",
    estimated_credits: 8,
    languages: ["Python"],
  },
  {
    id: "discord-bot",
    name: "Discord Bot",
    description: "Bot with slash commands and database",
    icon: "message-circle",
    estimated_credits: 12,
    languages: ["Python", "SQL"],
  },
  {
    id: "data-pipeline",
    name: "Data Pipeline",
    description: "ETL with scheduling and monitoring",
    icon: "database",
    estimated_credits: 14,
    languages: ["Python", "SQL"],
  },
  {
    id: "mobile-api",
    name: "Mobile API",
    description: "Backend API for mobile clients",
    icon: "smartphone",
    estimated_credits: 16,
    languages: ["Python", "TypeScript"],
  },
  {
    id: "ecommerce",
    name: "E-commerce",
    description: "Store with cart, checkout, payments",
    icon: "shopping-cart",
    estimated_credits: 28,
    languages: ["TypeScript", "Python", "SQL"],
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function nfetGradeFromScore(score: number): string {
  if (score >= 0.85) return "A";
  if (score >= 0.7) return "B";
  if (score >= 0.5) return "C";
  return "D";
}

function gradeColor(grade: string): string {
  if (grade === "A") return "text-codey-green";
  if (grade === "B") return "text-emerald-400";
  if (grade === "C") return "text-codey-yellow";
  return "text-codey-red";
}

function gradeBgColor(grade: string): string {
  if (grade === "A") return "bg-codey-green/20 border-codey-green/30";
  if (grade === "B") return "bg-emerald-400/20 border-emerald-400/30";
  if (grade === "C") return "bg-codey-yellow/20 border-codey-yellow/30";
  return "bg-codey-red/20 border-codey-red/30";
}

function monacoLangFromPath(filePath: string): string {
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
    toml: "toml",
    dockerfile: "dockerfile",
  };
  return map[ext || ""] || "plaintext";
}

// ── NFET Gauge Component ──────────────────────────────────────────────────────

function NfetGauge({
  label,
  value,
  max = 1,
  color = "codey-green",
}: {
  label: string;
  value: number;
  max?: number;
  color?: string;
}) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  const barColor =
    pct >= 70
      ? "bg-codey-green"
      : pct >= 40
        ? "bg-codey-yellow"
        : "bg-codey-red";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wider text-codey-text-muted">
          {label}
        </span>
        <span className="font-mono text-xs font-semibold text-codey-text">
          {value.toFixed(3)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-codey-border/50">
        <div
          className={`h-full rounded-full transition-all duration-700 ease-out ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── File Tree Component ───────────────────────────────────────────────────────

function FileTreeItem({
  node,
  depth = 0,
  fileStatuses,
  activeFilePath,
  onSelect,
}: {
  node: FileTreeNode;
  depth?: number;
  fileStatuses: Map<string, BuildFileStatus>;
  activeFilePath: string | null;
  onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 2);

  if (node.type === "directory") {
    return (
      <div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs hover:bg-codey-card-hover"
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-codey-text-muted" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-codey-text-muted" />
          )}
          <Folder className="h-3.5 w-3.5 shrink-0 text-codey-yellow" />
          <span className="truncate text-codey-text-dim">{node.name}</span>
        </button>
        {expanded && node.children && (
          <div>
            {node.children.map((child, i) => (
              <FileTreeItem
                key={`${node.name}/${child.name}-${i}`}
                node={child}
                depth={depth + 1}
                fileStatuses={fileStatuses}
                activeFilePath={activeFilePath}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  // File node
  const fullPath = node.name;
  const status = fileStatuses.get(fullPath);
  const isActive = activeFilePath === fullPath;

  let statusDot = "bg-codey-text-muted/30"; // pending / gray
  if (status?.status === "completed") statusDot = "bg-codey-green";
  else if (status?.status === "generating") statusDot = "bg-codey-yellow animate-pulse";
  else if (status?.status === "failed") statusDot = "bg-codey-red";

  return (
    <button
      onClick={() => onSelect(fullPath)}
      className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs transition-colors ${
        isActive
          ? "bg-codey-green/10 text-codey-green"
          : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
      }`}
      style={{ paddingLeft: `${depth * 12 + 8}px` }}
    >
      <div className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot}`} />
      <FileCode className="h-3.5 w-3.5 shrink-0 text-codey-text-muted" />
      <span className="truncate">{node.name}</span>
      {status?.line_count != null && (
        <span className="ml-auto shrink-0 text-[10px] text-codey-text-muted">
          {status.line_count}L
        </span>
      )}
    </button>
  );
}

// ── Phase Progress Bar ────────────────────────────────────────────────────────

function PhaseProgressBar({
  currentPhase,
  totalPhases,
  phases,
}: {
  currentPhase: number;
  totalPhases: number;
  phases: PlanPhase[];
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-medium uppercase tracking-wider text-codey-text-muted">
          Phase Progress
        </span>
        <span className="font-mono text-codey-text-dim">
          {currentPhase}/{totalPhases}
        </span>
      </div>
      <div className="flex gap-1">
        {phases.map((phase) => (
          <div
            key={phase.phase}
            className={`h-1.5 flex-1 rounded-full transition-all duration-500 ${
              phase.phase < currentPhase
                ? "bg-codey-green"
                : phase.phase === currentPhase
                  ? "bg-codey-yellow animate-pulse"
                  : "bg-codey-border/50"
            }`}
          />
        ))}
      </div>
      {phases[currentPhase - 1] && (
        <p className="text-[11px] text-codey-text-muted">
          {phases[currentPhase - 1].name}
        </p>
      )}
    </div>
  );
}

// ── Main Build Page ───────────────────────────────────────────────────────────

export default function BuildPage() {
  const { user } = useAuth();

  // Core state
  const [buildState, setBuildState] = useState<BuildState>("DESCRIBE");
  const [description, setDescription] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  // Clarify state
  const [questions, setQuestions] = useState<ClarificationQuestion[]>([]);
  const [defaults, setDefaults] = useState<Record<string, string>>({});
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [templateMatch, setTemplateMatch] = useState<TemplateMatch | null>(null);

  // Plan state
  const [plan, setPlan] = useState<BuildPlan | null>(null);

  // Building state
  const [projectId, setProjectId] = useState<string | null>(null);
  const [fileStatuses, setFileStatuses] = useState<Map<string, BuildFileStatus>>(new Map());
  const [activeFilePath, setActiveFilePath] = useState<string | null>(null);
  const [activeFileContent, setActiveFileContent] = useState("");
  const [activeFileLineCount, setActiveFileLineCount] = useState(0);
  const [currentPhase, setCurrentPhase] = useState(1);
  const [filesCompleted, setFilesCompleted] = useState(0);
  const [totalFiles, setTotalFiles] = useState(0);
  const [creditsUsed, setCreditsUsed] = useState(0);
  const [linesGenerated, setLinesGenerated] = useState(0);
  const [nfetHealth, setNfetHealth] = useState<NfetHealth | null>(null);
  const [interventionAlerts, setInterventionAlerts] = useState<string[]>([]);
  const [wsConnected, setWsConnected] = useState(false);

  // Checkpoint state
  const [checkpoint, setCheckpoint] = useState<CheckpointData | null>(null);
  const [expandedTests, setExpandedTests] = useState<Set<number>>(new Set());
  const [reviewingCode, setReviewingCode] = useState(false);

  // Complete state
  const [completionStats, setCompletionStats] = useState<CompletionStats | null>(null);

  // Loading states
  const [submitting, setSubmitting] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [approving, setApproving] = useState(false);

  // WebSocket ref
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Code output buffer ref for streaming
  const codeBufferRef = useRef<Map<string, string>>(new Map());

  // ── WebSocket connection ────────────────────────────────────────────────────

  const connectWebSocket = useCallback(
    (pid: string) => {
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }

      const token =
        typeof window !== "undefined"
          ? localStorage.getItem("codey_token")
          : null;

      const wsBase =
        process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
      const url = `${wsBase}/build/${pid}/stream${
        token ? `?token=${encodeURIComponent(token)}` : ""
      }`;

      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as BuildStreamMessage;
          handleStreamMessage(msg);
        } catch {
          console.error("Failed to parse build stream message");
        }
      };

      ws.onerror = () => {
        setWsConnected(false);
      };

      ws.onclose = (event) => {
        setWsConnected(false);
        if (event.code !== 1000 && event.code !== 1008 && buildState === "BUILDING") {
          reconnectTimerRef.current = setTimeout(() => {
            connectWebSocket(pid);
          }, 3000);
        }
      };
    },
    [buildState]
  );

  // Clean up WebSocket on unmount
  useEffect(() => {
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, []);

  // ── Stream message handler ──────────────────────────────────────────────────

  const handleStreamMessage = useCallback(
    (msg: BuildStreamMessage) => {
      const data = msg.data;

      switch (msg.type) {
        case "status":
          // General status update
          break;

        case "phase":
          setCurrentPhase(data.phase as number);
          break;

        case "file_start": {
          const filePath = data.file_path as string;
          setFileStatuses((prev) => {
            const next = new Map(prev);
            next.set(filePath, {
              ...(next.get(filePath) || {
                id: "",
                file_path: filePath,
                line_count: null,
                phase: data.phase as number | null,
                status: "generating",
                stress_score: null,
                validation_passed: null,
              }),
              status: "generating",
            });
            return next;
          });
          setActiveFilePath(filePath);
          setActiveFileContent("");
          codeBufferRef.current.set(filePath, "");
          break;
        }

        case "file_chunk": {
          const fp = data.file_path as string;
          const chunk = data.content as string;
          const existing = codeBufferRef.current.get(fp) || "";
          const updated = existing + chunk;
          codeBufferRef.current.set(fp, updated);
          if (fp === activeFilePath || !activeFilePath) {
            setActiveFileContent(updated);
            setActiveFileLineCount(updated.split("\n").length);
          }
          break;
        }

        case "file_complete": {
          const donePath = data.file_path as string;
          const lineCount = data.line_count as number;
          setFileStatuses((prev) => {
            const next = new Map(prev);
            const existing = next.get(donePath);
            next.set(donePath, {
              ...(existing || {
                id: (data.file_id as string) || "",
                file_path: donePath,
                line_count: lineCount,
                phase: data.phase as number | null,
                status: "completed",
                stress_score: (data.stress_score as number) || null,
                validation_passed: (data.validation_passed as boolean) || null,
              }),
              status: "completed",
              line_count: lineCount,
              stress_score: (data.stress_score as number) || null,
              validation_passed: (data.validation_passed as boolean) || null,
            });
            return next;
          });
          setFilesCompleted((prev) => prev + 1);
          setLinesGenerated((prev) => prev + lineCount);
          break;
        }

        case "checkpoint": {
          const cpData: CheckpointData = {
            phase: data.phase as number,
            phase_name: data.phase_name as string,
            files_built: data.files_built as number,
            lines_generated: data.lines_generated as number,
            tests_passed: data.tests_passed as number,
            tests_failed: data.tests_failed as number,
            test_details: (data.test_details as CheckpointData["test_details"]) || [],
            nfet: (data.nfet as NfetHealth) || null,
          };
          setCheckpoint(cpData);
          if (cpData.nfet) setNfetHealth(cpData.nfet);
          setBuildState("CHECKPOINT");
          break;
        }

        case "nfet": {
          const nfet: NfetHealth = {
            es_score: data.es_score as number,
            kappa: data.kappa as number,
            sigma: data.sigma as number,
            phase: data.phase as string,
          };
          setNfetHealth(nfet);

          // Check for architecture interventions
          if (nfet.es_score < 0.4) {
            setInterventionAlerts((prev) => [
              ...prev.slice(-4),
              `Architecture stress detected: ES=${nfet.es_score.toFixed(3)}. Codey is restructuring.`,
            ]);
          }
          break;
        }

        case "error": {
          const errMsg = data.message as string;
          setInterventionAlerts((prev) => [
            ...prev.slice(-4),
            `Error: ${errMsg}`,
          ]);
          break;
        }

        case "complete": {
          const stats: CompletionStats = {
            total_files: data.total_files as number,
            total_lines: data.total_lines as number,
            languages: (data.languages as string[]) || [],
            test_coverage: (data.test_coverage as number) || 0,
            nfet_grade: (data.nfet_grade as string) || "B",
            nfet_es_score: (data.nfet_es_score as number) || 0,
            credits_charged: (data.credits_charged as number) || 0,
          };
          setCompletionStats(stats);
          setBuildState("COMPLETE");
          break;
        }
      }
    },
    [activeFilePath]
  );

  // ── Handlers ────────────────────────────────────────────────────────────────

  async function handleStartPlanning() {
    if (!description.trim()) return;
    setSubmitting(true);
    try {
      const result = await api.post<{
        questions: ClarificationQuestion[];
        defaults: Record<string, string>;
        template_match: TemplateMatch | null;
      }>("/build/start", { description });

      setQuestions(result.questions);
      setDefaults(result.defaults);
      setAnswers({ ...result.defaults });
      setTemplateMatch(result.template_match);
      setBuildState("CLARIFY");
    } catch (err) {
      console.error("Failed to start planning:", err);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleBuildWithAnswers() {
    setPlanLoading(true);
    try {
      const result = await api.post<BuildPlan>("/build/plan", {
        description,
        answers,
      });
      setPlan(result);
      setProjectId(result.project_id);
      setTotalFiles(result.total_files);
      setBuildState("PLAN");
    } catch (err) {
      console.error("Failed to create plan:", err);
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleApprove() {
    if (!projectId) return;
    setApproving(true);
    try {
      await api.post(`/build/approve/${projectId}`);
      setBuildState("BUILDING");
      connectWebSocket(projectId);
    } catch (err) {
      console.error("Failed to approve build:", err);
    } finally {
      setApproving(false);
    }
  }

  async function handleCheckpointAction(action: "continue" | "review" | "modify") {
    if (action === "review") {
      setReviewingCode(true);
      return;
    }

    if (!projectId || !checkpoint) return;

    try {
      await api.post(`/build/${projectId}/checkpoint/${checkpoint.phase}`, {
        action,
      });

      if (action === "continue") {
        setReviewingCode(false);
        setBuildState("BUILDING");
      } else if (action === "modify") {
        // Stay in checkpoint with modification mode
      }
    } catch (err) {
      console.error("Failed to handle checkpoint:", err);
    }
  }

  async function handleDownloadZip() {
    if (!projectId) return;
    try {
      const result = await api.get<{ download_url: string; filename: string }>(
        `/build/${projectId}/download`
      );
      // Trigger download
      const a = document.createElement("a");
      a.href = result.download_url;
      a.download = result.filename;
      a.click();
    } catch (err) {
      console.error("Failed to get download:", err);
    }
  }

  function handleSelectFile(path: string) {
    setActiveFilePath(path);
    const content = codeBufferRef.current.get(path) || "";
    setActiveFileContent(content);
    setActiveFileLineCount(content.split("\n").length);
  }

  function handleReset() {
    setBuildState("DESCRIBE");
    setDescription("");
    setSelectedTemplate(null);
    setQuestions([]);
    setDefaults({});
    setAnswers({});
    setTemplateMatch(null);
    setPlan(null);
    setProjectId(null);
    setFileStatuses(new Map());
    setActiveFilePath(null);
    setActiveFileContent("");
    setActiveFileLineCount(0);
    setCurrentPhase(1);
    setFilesCompleted(0);
    setTotalFiles(0);
    setCreditsUsed(0);
    setLinesGenerated(0);
    setNfetHealth(null);
    setInterventionAlerts([]);
    setCheckpoint(null);
    setCompletionStats(null);
    setReviewingCode(false);
    codeBufferRef.current.clear();
  }

  function handleTemplateSelect(templateId: string) {
    setSelectedTemplate(templateId);
    const tpl = TEMPLATES.find((t) => t.id === templateId);
    if (tpl) {
      setDescription((prev) =>
        prev
          ? prev
          : `Build a ${tpl.name.toLowerCase()} project`
      );
    }
  }

  // ── Credit check ────────────────────────────────────────────────────────────

  const hasCredits = (user?.credits_remaining ?? 0) > 0;

  // ══════════════════════════════════════════════════════════════════════════════
  // DESCRIBE STATE
  // ══════════════════════════════════════════════════════════════════════════════

  if (buildState === "DESCRIBE") {
    return (
      <div className="mx-auto max-w-5xl space-y-8">
        {/* Header */}
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-codey-green/20 to-codey-green/5 ring-1 ring-codey-green/20">
            <Layers className="h-7 w-7 text-codey-green" />
          </div>
          <h1 className="text-3xl font-bold text-codey-text">Build Mode</h1>
          <p className="mt-2 text-sm text-codey-text-dim">
            Describe your project and Codey will plan, build, and test it end-to-end.
          </p>
        </div>

        {/* Description textarea */}
        <div className="rounded-2xl border border-codey-border bg-codey-card shadow-lg shadow-black/20">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={`Describe the project you want Codey to build...\n\nBe specific about:\n- What the project does\n- Key features and functionality\n- Any technical requirements (language, framework, database)\n- APIs or integrations needed`}
            rows={8}
            className="w-full resize-none rounded-t-2xl border-none bg-transparent px-6 py-5 text-sm leading-relaxed text-codey-text placeholder:text-codey-text-muted focus:outline-none focus:ring-0"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                handleStartPlanning();
              }
            }}
          />
          <div className="flex items-center justify-between border-t border-codey-border/50 px-6 py-3">
            <span className="text-xs text-codey-text-muted">
              {description.length > 0
                ? `${description.length} characters`
                : "Cmd+Enter to submit"}
            </span>
            {!hasCredits && (
              <span className="text-xs text-codey-red">
                No credits remaining.{" "}
                <a href="/credits" className="underline">
                  Top up
                </a>
              </span>
            )}
          </div>
        </div>

        {/* Templates Grid */}
        <div>
          <h2 className="mb-4 text-sm font-semibold text-codey-text">
            Or start from a template
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {TEMPLATES.map((tpl) => {
              const Icon = TEMPLATE_ICONS[tpl.icon] || Package;
              const isSelected = selectedTemplate === tpl.id;
              return (
                <button
                  key={tpl.id}
                  onClick={() => handleTemplateSelect(tpl.id)}
                  className={`group relative rounded-xl border p-4 text-left transition-all ${
                    isSelected
                      ? "border-codey-green bg-codey-green/5 ring-1 ring-codey-green/30"
                      : "border-codey-border bg-codey-card hover:border-codey-border-light hover:bg-codey-card-hover"
                  }`}
                >
                  <div
                    className={`mb-3 flex h-9 w-9 items-center justify-center rounded-lg ${
                      isSelected
                        ? "bg-codey-green/20 text-codey-green"
                        : "bg-codey-bg text-codey-text-dim group-hover:text-codey-text"
                    }`}
                  >
                    <Icon className="h-4.5 w-4.5" />
                  </div>
                  <p
                    className={`text-sm font-medium ${
                      isSelected ? "text-codey-green" : "text-codey-text"
                    }`}
                  >
                    {tpl.name}
                  </p>
                  <p className="mt-0.5 text-[11px] text-codey-text-muted line-clamp-2">
                    {tpl.description}
                  </p>
                  <div className="mt-2.5 flex items-center gap-1 text-[10px] text-codey-text-muted">
                    <Zap className="h-2.5 w-2.5" />~{tpl.estimated_credits} credits
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Start button */}
        <div className="flex justify-end">
          <button
            onClick={handleStartPlanning}
            disabled={!description.trim() || !hasCredits || submitting}
            className="flex items-center gap-2.5 rounded-xl bg-codey-green px-8 py-3.5 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Analyzing...
              </>
            ) : (
              <>
                Start planning
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // CLARIFY STATE
  // ══════════════════════════════════════════════════════════════════════════════

  if (buildState === "CLARIFY") {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">
            Clarification
          </h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Codey has a few questions to make sure the build matches your vision.
          </p>
        </div>

        {/* Template match */}
        {templateMatch && (
          <div className="flex items-center gap-3 rounded-xl border border-codey-green/20 bg-codey-green/5 px-5 py-3">
            <Check className="h-4 w-4 text-codey-green" />
            <span className="text-sm text-codey-text">
              Matched template:{" "}
              <span className="font-semibold text-codey-green">
                {templateMatch.name}
              </span>
              <span className="ml-2 text-xs text-codey-text-muted">
                ({Math.round(templateMatch.confidence * 100)}% confidence)
              </span>
            </span>
          </div>
        )}

        {/* Questions */}
        <div className="space-y-4">
          {questions.map((q) => (
            <div
              key={q.id}
              className="rounded-xl border border-codey-border bg-codey-card p-5"
            >
              <label className="mb-3 block text-sm font-medium text-codey-text">
                {q.question}
              </label>
              {q.options ? (
                <div className="flex flex-wrap gap-2">
                  {q.options.map((opt) => (
                    <button
                      key={opt}
                      onClick={() =>
                        setAnswers((prev) => ({ ...prev, [q.id]: opt }))
                      }
                      className={`rounded-lg border px-4 py-2 text-sm transition-all ${
                        answers[q.id] === opt
                          ? "border-codey-green bg-codey-green/10 text-codey-green"
                          : "border-codey-border text-codey-text-dim hover:border-codey-border-light hover:bg-codey-card-hover"
                      }`}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              ) : (
                <input
                  type="text"
                  value={answers[q.id] || ""}
                  onChange={(e) =>
                    setAnswers((prev) => ({
                      ...prev,
                      [q.id]: e.target.value,
                    }))
                  }
                  placeholder={q.default || ""}
                  className="w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              )}
              {q.default && answers[q.id] !== q.default && (
                <p className="mt-2 text-xs text-codey-text-muted">
                  Default: {q.default}
                </p>
              )}
            </div>
          ))}
        </div>

        {/* Defaults summary */}
        <div className="rounded-xl border border-codey-border bg-codey-card p-5">
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-codey-text-muted">
            Codey will use these defaults
          </h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Object.entries(defaults).map(([key, value]) => (
              <div key={key} className="rounded-lg bg-codey-bg px-3 py-2">
                <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
                  {key.replace(/_/g, " ")}
                </span>
                <span className="text-xs font-medium text-codey-text">
                  {answers[key] || value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setBuildState("DESCRIBE")}
            className="rounded-lg border border-codey-border px-5 py-2.5 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            Back
          </button>
          <div className="flex-1" />
          <button
            onClick={handleBuildWithAnswers}
            disabled={planLoading}
            className="flex items-center gap-2 rounded-xl bg-codey-green px-8 py-3 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
          >
            {planLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Planning...
              </>
            ) : (
              <>
                Build with these answers
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // PLAN STATE
  // ══════════════════════════════════════════════════════════════════════════════

  if (buildState === "PLAN" && plan) {
    return (
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-codey-text">Build Plan</h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Review the plan before Codey starts building.
          </p>
        </div>

        {/* Project summary */}
        <div className="rounded-2xl border border-codey-border bg-codey-card p-6">
          <h2 className="text-lg font-bold text-codey-text">{plan.name}</h2>
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="rounded-lg bg-codey-bg px-4 py-3">
              <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
                Files
              </span>
              <span className="text-xl font-bold text-codey-text">
                {plan.total_files}
              </span>
            </div>
            <div className="rounded-lg bg-codey-bg px-4 py-3">
              <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
                Est. Lines
              </span>
              <span className="text-xl font-bold text-codey-text">
                {plan.estimated_lines.toLocaleString()}
              </span>
            </div>
            <div className="rounded-lg bg-codey-bg px-4 py-3">
              <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
                Phases
              </span>
              <span className="text-xl font-bold text-codey-text">
                {plan.phases.length}
              </span>
            </div>
            <div className="rounded-lg bg-codey-bg px-4 py-3">
              <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
                Credits
              </span>
              <span className="text-xl font-bold text-codey-green">
                ~{plan.estimated_credits}
              </span>
            </div>
          </div>
        </div>

        {/* Stack */}
        <div className="rounded-xl border border-codey-border bg-codey-card p-5">
          <h3 className="mb-3 text-sm font-semibold text-codey-text">Stack</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(plan.stack).map(([key, value]) => (
              <span
                key={key}
                className="rounded-lg bg-codey-bg px-3 py-1.5 text-xs text-codey-text-dim"
              >
                <span className="text-codey-text-muted">{key}:</span>{" "}
                <span className="font-medium text-codey-text">
                  {String(value)}
                </span>
              </span>
            ))}
          </div>
        </div>

        {/* File tree */}
        <div className="rounded-xl border border-codey-border bg-codey-card">
          <div className="border-b border-codey-border px-5 py-3">
            <h3 className="text-sm font-semibold text-codey-text">
              Project Structure
            </h3>
          </div>
          <div className="max-h-64 overflow-y-auto p-3">
            {plan.file_tree.map((node, i) => (
              <FileTreeItem
                key={`${node.name}-${i}`}
                node={node}
                fileStatuses={fileStatuses}
                activeFilePath={null}
                onSelect={() => {}}
              />
            ))}
          </div>
        </div>

        {/* Phases */}
        <div className="rounded-xl border border-codey-border bg-codey-card">
          <div className="border-b border-codey-border px-5 py-3">
            <h3 className="text-sm font-semibold text-codey-text">
              Build Phases
            </h3>
          </div>
          <div className="divide-y divide-codey-border/50">
            {plan.phases.map((phase) => (
              <div key={phase.phase} className="px-5 py-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-codey-green/10 text-xs font-bold text-codey-green">
                    {phase.phase}
                  </div>
                  <div>
                    <p className="text-sm font-medium text-codey-text">
                      {phase.name}
                    </p>
                    <p className="text-xs text-codey-text-muted">
                      {phase.files.length} files
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setBuildState("CLARIFY")}
            className="rounded-lg border border-codey-border px-5 py-2.5 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            Request Changes
          </button>
          <button
            onClick={handleReset}
            className="rounded-lg border border-codey-red/30 px-5 py-2.5 text-sm text-codey-red transition-colors hover:bg-codey-red/10"
          >
            Cancel
          </button>
          <div className="flex-1" />
          <button
            onClick={handleApprove}
            disabled={approving}
            className="flex items-center gap-2 rounded-xl bg-codey-green px-8 py-3 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
          >
            {approving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting build...
              </>
            ) : (
              <>
                <Play className="h-4 w-4" />
                Approve & Build
              </>
            )}
          </button>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // BUILDING STATE
  // ══════════════════════════════════════════════════════════════════════════════

  if (buildState === "BUILDING" && plan) {
    return (
      <div className="flex h-[calc(100vh-8rem)] flex-col gap-0 overflow-hidden">
        {/* 3-Panel Layout */}
        <div className="flex flex-1 gap-px overflow-hidden rounded-xl border border-codey-border bg-codey-border">
          {/* LEFT PANEL - File Tree */}
          <div className="flex w-64 shrink-0 flex-col bg-codey-card">
            <div className="border-b border-codey-border px-4 py-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-codey-text-muted">
                Files
              </h3>
            </div>

            {/* Phase progress */}
            <div className="border-b border-codey-border/50 px-4 py-3">
              <PhaseProgressBar
                currentPhase={currentPhase}
                totalPhases={plan.phases.length}
                phases={plan.phases}
              />
            </div>

            {/* File tree */}
            <div className="flex-1 overflow-y-auto p-2">
              {plan.file_tree.map((node, i) => (
                <FileTreeItem
                  key={`${node.name}-${i}`}
                  node={node}
                  fileStatuses={fileStatuses}
                  activeFilePath={activeFilePath}
                  onSelect={handleSelectFile}
                />
              ))}
            </div>
          </div>

          {/* CENTER PANEL - Code Output */}
          <div className="flex flex-1 flex-col bg-codey-bg">
            {/* File header */}
            <div className="flex items-center justify-between border-b border-codey-border bg-codey-card px-4 py-2">
              <div className="flex items-center gap-2">
                <Code2 className="h-3.5 w-3.5 text-codey-text-muted" />
                <span className="text-xs font-medium text-codey-text">
                  {activeFilePath || "No file selected"}
                </span>
              </div>
              {activeFileLineCount > 0 && (
                <span className="text-[10px] text-codey-text-muted">
                  {activeFileLineCount} lines
                </span>
              )}
            </div>

            {/* Monaco editor */}
            <div className="flex-1">
              {activeFileContent ? (
                <MonacoEditor
                  height="100%"
                  language={
                    activeFilePath
                      ? monacoLangFromPath(activeFilePath)
                      : "plaintext"
                  }
                  value={activeFileContent}
                  theme="vs-dark"
                  options={{
                    readOnly: true,
                    minimap: { enabled: false },
                    fontSize: 12,
                    fontFamily: "JetBrains Mono, Fira Code, monospace",
                    lineNumbers: "on",
                    scrollBeyondLastLine: false,
                    padding: { top: 12, bottom: 12 },
                    renderLineHighlight: "none",
                    wordWrap: "on",
                    overviewRulerLanes: 0,
                    lineDecorationsWidth: 0,
                    glyphMargin: false,
                    folding: false,
                  }}
                />
              ) : (
                <div className="flex h-full items-center justify-center">
                  <div className="text-center">
                    <Loader2 className="mx-auto mb-3 h-6 w-6 animate-spin text-codey-green" />
                    <p className="text-sm text-codey-text-dim">
                      Generating code...
                    </p>
                    <p className="mt-1 text-xs text-codey-text-muted">
                      Streaming output will appear here
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* RIGHT PANEL - NFET Health & Alerts */}
          <div className="flex w-72 shrink-0 flex-col bg-codey-card">
            <div className="border-b border-codey-border px-4 py-3">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-codey-text-muted">
                  Health Monitor
                </h3>
                {wsConnected ? (
                  <Wifi className="h-3 w-3 text-codey-green" />
                ) : (
                  <WifiOff className="h-3 w-3 text-codey-red" />
                )}
              </div>
            </div>

            {/* NFET Gauges */}
            <div className="space-y-4 border-b border-codey-border/50 px-4 py-4">
              <NfetGauge
                label="ES Score"
                value={nfetHealth?.es_score ?? 0}
              />
              <NfetGauge
                label="Kappa"
                value={nfetHealth?.kappa ?? 0}
              />
              <NfetGauge
                label="Sigma"
                value={nfetHealth?.sigma ?? 0}
              />
            </div>

            {/* Phase badge */}
            {nfetHealth?.phase && (
              <div className="border-b border-codey-border/50 px-4 py-3">
                <span className="text-[10px] uppercase tracking-wider text-codey-text-muted">
                  NFET Phase
                </span>
                <div className="mt-1 flex items-center gap-2">
                  <Cpu className="h-3.5 w-3.5 text-codey-text-dim" />
                  <span className="text-xs font-medium text-codey-text">
                    {nfetHealth.phase}
                  </span>
                </div>
              </div>
            )}

            {/* Architecture intervention alerts */}
            <div className="flex-1 overflow-y-auto px-4 py-3">
              <span className="text-[10px] uppercase tracking-wider text-codey-text-muted">
                Alerts
              </span>
              {interventionAlerts.length === 0 ? (
                <div className="mt-3 flex items-center gap-2 text-xs text-codey-text-muted">
                  <Shield className="h-3.5 w-3.5 text-codey-green" />
                  All systems healthy
                </div>
              ) : (
                <div className="mt-2 space-y-2">
                  {interventionAlerts.map((alert, i) => (
                    <div
                      key={i}
                      className="rounded-lg border border-codey-red/20 bg-codey-red/5 px-3 py-2 text-[11px] text-codey-red"
                    >
                      <AlertTriangle className="mb-0.5 mr-1 inline h-3 w-3" />
                      {alert}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* BOTTOM BAR */}
        <div className="mt-px flex items-center gap-6 rounded-b-xl border border-t-0 border-codey-border bg-codey-card px-5 py-2.5">
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 animate-pulse rounded-full bg-codey-yellow" />
            <span className="text-xs font-medium text-codey-text">
              Phase {currentPhase}: {plan.phases[currentPhase - 1]?.name || "Building"}
            </span>
          </div>
          <div className="h-4 w-px bg-codey-border" />
          <span className="text-xs text-codey-text-dim">
            <FileCode className="mr-1 inline h-3 w-3" />
            {filesCompleted}/{totalFiles} files
          </span>
          <div className="h-4 w-px bg-codey-border" />
          <span className="text-xs text-codey-text-dim">
            <Zap className="mr-1 inline h-3 w-3 text-codey-green" />
            {creditsUsed || plan.estimated_credits} credits
          </span>
          <div className="h-4 w-px bg-codey-border" />
          <span className="text-xs text-codey-text-dim">
            {linesGenerated.toLocaleString()} lines
          </span>
          <div className="flex-1" />
          <span className="text-[10px] text-codey-text-muted">
            Est. remaining: ~{Math.max(0, totalFiles - filesCompleted)} files
          </span>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // CHECKPOINT STATE
  // ══════════════════════════════════════════════════════════════════════════════

  if (buildState === "CHECKPOINT" && checkpoint) {
    return (
      <div className="mx-auto max-w-4xl space-y-6">
        {/* Phase header */}
        <div className="rounded-2xl border border-codey-yellow/20 bg-codey-yellow/5 p-6">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-codey-yellow/20">
              <Shield className="h-5 w-5 text-codey-yellow" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-codey-text">
                Checkpoint: {checkpoint.phase_name}
              </h1>
              <p className="text-sm text-codey-text-dim">
                Phase {checkpoint.phase} complete. Review before continuing.
              </p>
            </div>
          </div>
        </div>

        {/* Phase summary */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div className="rounded-xl border border-codey-border bg-codey-card px-4 py-3">
            <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
              Files Built
            </span>
            <span className="text-2xl font-bold text-codey-text">
              {checkpoint.files_built}
            </span>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card px-4 py-3">
            <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
              Lines Generated
            </span>
            <span className="text-2xl font-bold text-codey-text">
              {checkpoint.lines_generated.toLocaleString()}
            </span>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card px-4 py-3">
            <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
              Tests Passed
            </span>
            <span className="text-2xl font-bold text-codey-green">
              {checkpoint.tests_passed}
            </span>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card px-4 py-3">
            <span className="block text-[10px] uppercase tracking-wider text-codey-text-muted">
              Tests Failed
            </span>
            <span
              className={`text-2xl font-bold ${
                checkpoint.tests_failed > 0 ? "text-codey-red" : "text-codey-text-muted"
              }`}
            >
              {checkpoint.tests_failed}
            </span>
          </div>
        </div>

        {/* Test results */}
        {checkpoint.test_details.length > 0 && (
          <div className="rounded-xl border border-codey-border bg-codey-card">
            <div className="border-b border-codey-border px-5 py-3">
              <h3 className="text-sm font-semibold text-codey-text">
                Test Results
              </h3>
            </div>
            <div className="divide-y divide-codey-border/50">
              {checkpoint.test_details.map((test, i) => (
                <div key={i}>
                  <button
                    onClick={() => {
                      setExpandedTests((prev) => {
                        const next = new Set(prev);
                        if (next.has(i)) next.delete(i);
                        else next.add(i);
                        return next;
                      });
                    }}
                    className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-codey-card-hover"
                  >
                    {test.passed ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-codey-green" />
                    ) : (
                      <X className="h-4 w-4 shrink-0 text-codey-red" />
                    )}
                    <span className="flex-1 text-sm text-codey-text-dim">
                      {test.name}
                    </span>
                    {test.detail && (
                      <ChevronDown
                        className={`h-3 w-3 text-codey-text-muted transition-transform ${
                          expandedTests.has(i) ? "rotate-180" : ""
                        }`}
                      />
                    )}
                  </button>
                  {expandedTests.has(i) && test.detail && (
                    <div className="border-t border-codey-border/30 bg-codey-bg px-5 py-3">
                      <pre className="text-xs text-codey-text-dim whitespace-pre-wrap font-mono">
                        {test.detail}
                      </pre>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* NFET health snapshot */}
        {checkpoint.nfet && (
          <div className="rounded-xl border border-codey-border bg-codey-card p-5">
            <h3 className="mb-4 text-sm font-semibold text-codey-text">
              NFET Health Snapshot
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <NfetGauge
                label="ES Score"
                value={checkpoint.nfet.es_score}
              />
              <NfetGauge label="Kappa" value={checkpoint.nfet.kappa} />
              <NfetGauge label="Sigma" value={checkpoint.nfet.sigma} />
            </div>
          </div>
        )}

        {/* Code review panel */}
        {reviewingCode && (
          <div className="rounded-xl border border-codey-border bg-codey-card">
            <div className="border-b border-codey-border px-5 py-3">
              <h3 className="text-sm font-semibold text-codey-text">
                Generated Code
              </h3>
            </div>
            <div className="flex">
              {/* File list */}
              <div className="w-48 shrink-0 border-r border-codey-border overflow-y-auto max-h-96">
                {Array.from(fileStatuses.entries())
                  .filter(([_, s]) => s.status === "completed")
                  .map(([path, s]) => (
                    <button
                      key={path}
                      onClick={() => handleSelectFile(path)}
                      className={`w-full px-3 py-2 text-left text-xs truncate transition-colors ${
                        activeFilePath === path
                          ? "bg-codey-green/10 text-codey-green"
                          : "text-codey-text-dim hover:bg-codey-card-hover"
                      }`}
                    >
                      {path.split("/").pop()}
                    </button>
                  ))}
              </div>
              {/* Code viewer */}
              <div className="flex-1">
                <MonacoEditor
                  height="384px"
                  language={
                    activeFilePath
                      ? monacoLangFromPath(activeFilePath)
                      : "plaintext"
                  }
                  value={activeFileContent}
                  theme="vs-dark"
                  options={{
                    readOnly: true,
                    minimap: { enabled: false },
                    fontSize: 12,
                    fontFamily: "JetBrains Mono, Fira Code, monospace",
                    lineNumbers: "on",
                    scrollBeyondLastLine: false,
                    padding: { top: 12, bottom: 12 },
                    renderLineHighlight: "none",
                    wordWrap: "on",
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => handleCheckpointAction("review")}
            className={`flex items-center gap-2 rounded-lg border px-5 py-2.5 text-sm transition-colors ${
              reviewingCode
                ? "border-codey-green/30 bg-codey-green/10 text-codey-green"
                : "border-codey-border text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
            }`}
          >
            <Eye className="h-4 w-4" />
            Review code
          </button>
          <button
            onClick={() => handleCheckpointAction("modify")}
            className="flex items-center gap-2 rounded-lg border border-codey-border px-5 py-2.5 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            <Pencil className="h-4 w-4" />
            Request modifications
          </button>
          <div className="flex-1" />
          <button
            onClick={() => handleCheckpointAction("continue")}
            className="flex items-center gap-2 rounded-xl bg-codey-green px-8 py-3 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green"
          >
            Continue to next phase
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // COMPLETE STATE
  // ══════════════════════════════════════════════════════════════════════════════

  if (buildState === "COMPLETE" && completionStats) {
    const grade = completionStats.nfet_grade;

    return (
      <div className="mx-auto max-w-3xl space-y-8">
        {/* Hero */}
        <div className="text-center">
          <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-3xl bg-codey-green/10 ring-2 ring-codey-green/20">
            <CheckCircle2 className="h-10 w-10 text-codey-green" />
          </div>
          <h1 className="text-3xl font-bold text-codey-text">
            Your project is ready.
          </h1>
          <p className="mt-2 text-sm text-codey-text-dim">
            Codey has finished building your project. Everything has been tested
            and validated.
          </p>
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div className="rounded-xl border border-codey-border bg-codey-card p-4 text-center">
            <span className="block text-2xl font-bold text-codey-text">
              {completionStats.total_files}
            </span>
            <span className="text-[11px] text-codey-text-muted">
              Total Files
            </span>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card p-4 text-center">
            <span className="block text-2xl font-bold text-codey-text">
              {completionStats.total_lines.toLocaleString()}
            </span>
            <span className="text-[11px] text-codey-text-muted">
              Total Lines
            </span>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card p-4 text-center">
            <span className="block text-2xl font-bold text-codey-text">
              {completionStats.languages.length}
            </span>
            <span className="text-[11px] text-codey-text-muted">
              Languages
            </span>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card p-4 text-center">
            <span className="block text-2xl font-bold text-codey-text">
              {completionStats.test_coverage}%
            </span>
            <span className="text-[11px] text-codey-text-muted">
              Test Coverage
            </span>
          </div>
        </div>

        {/* NFET Health Grade */}
        <div
          className={`flex items-center gap-5 rounded-2xl border p-6 ${gradeBgColor(grade)}`}
        >
          <div
            className={`flex h-16 w-16 items-center justify-center rounded-2xl bg-codey-bg text-3xl font-black ${gradeColor(grade)}`}
          >
            {grade}
          </div>
          <div>
            <p className="text-sm font-semibold text-codey-text">
              NFET Structural Health Grade
            </p>
            <p className="mt-0.5 text-xs text-codey-text-dim">
              ES Score: {completionStats.nfet_es_score.toFixed(3)}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {completionStats.languages.map((lang) => (
                <span
                  key={lang}
                  className="rounded-md bg-codey-bg px-2 py-0.5 text-[10px] font-medium text-codey-text-dim"
                >
                  {lang}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Download & GitHub */}
        <div className="flex flex-col gap-3 sm:flex-row">
          <button
            onClick={handleDownloadZip}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-codey-green px-6 py-4 text-sm font-bold text-codey-bg transition-all hover:shadow-glow-green"
          >
            <Download className="h-5 w-5" />
            Download ZIP
          </button>
          <button
            className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-codey-border bg-codey-card px-6 py-4 text-sm font-medium text-codey-text transition-colors hover:bg-codey-card-hover"
          >
            <Github className="h-5 w-5" />
            Push to GitHub
          </button>
        </div>

        {/* Credits charged */}
        <div className="rounded-xl border border-codey-border bg-codey-card p-5">
          <h3 className="mb-3 text-sm font-semibold text-codey-text">
            Credits Charged
          </h3>
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-codey-text-dim">Code generation</span>
              <span className="font-mono text-codey-text">
                {Math.round(completionStats.credits_charged * 0.7)}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-codey-text-dim">NFET analysis</span>
              <span className="font-mono text-codey-text">
                {Math.round(completionStats.credits_charged * 0.15)}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-codey-text-dim">Test generation</span>
              <span className="font-mono text-codey-text">
                {Math.round(completionStats.credits_charged * 0.15)}
              </span>
            </div>
            <div className="mt-2 flex items-center justify-between border-t border-codey-border/50 pt-2 text-sm">
              <span className="font-medium text-codey-text">Total</span>
              <span className="font-mono font-bold text-codey-green">
                {completionStats.credits_charged}
              </span>
            </div>
          </div>
        </div>

        {/* Continue building */}
        <div className="text-center">
          <button
            onClick={handleReset}
            className="flex mx-auto items-center gap-2 rounded-lg border border-codey-border px-6 py-3 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            <RotateCcw className="h-4 w-4" />
            Build another project
          </button>
        </div>
      </div>
    );
  }

  // ── Fallback ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-64 items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
    </div>
  );
}
