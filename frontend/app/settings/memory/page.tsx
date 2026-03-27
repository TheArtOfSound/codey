"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  Brain,
  Edit3,
  Trash2,
  Plus,
  Download,
  AlertTriangle,
  Check,
  X,
  Clock,
  Code,
  Palette,
  MessageSquare,
  Target,
  Layers,
  Workflow,
  User,
  Loader2,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface MemoryItem {
  id: string;
  dimension: string;
  key: string;
  value: string;
  updated_at: string;
}

interface MemoryUpdate {
  id: string;
  dimension: string;
  action: "added" | "updated" | "removed";
  key: string;
  value: string;
  timestamp: string;
}

interface MemoryDimension {
  key: string;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}

const DIMENSIONS: MemoryDimension[] = [
  {
    key: "language_preferences",
    label: "Language & Framework Preferences",
    description: "Languages, frameworks, and tools you prefer",
    icon: Code,
  },
  {
    key: "coding_style",
    label: "Coding Style",
    description: "Naming conventions, formatting, comment style",
    icon: Palette,
  },
  {
    key: "communication",
    label: "Communication Preferences",
    description: "How you like explanations and responses",
    icon: MessageSquare,
  },
  {
    key: "project_context",
    label: "Project Context",
    description: "Active projects, architectures, and patterns",
    icon: Target,
  },
  {
    key: "error_patterns",
    label: "Error Patterns",
    description: "Common issues and how to avoid them",
    icon: AlertTriangle,
  },
  {
    key: "workflow",
    label: "Workflow",
    description: "Git habits, CI/CD preferences, deployment patterns",
    icon: Workflow,
  },
  {
    key: "personal",
    label: "Personal Preferences",
    description: "Timezone, editor, OS, and other personal context",
    icon: User,
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTimestamp(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ── Memory Item Row ───────────────────────────────────────────────────────────

function MemoryItemRow({
  item,
  onEdit,
  onDelete,
}: {
  item: MemoryItem;
  onEdit: (id: string, value: string) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(item.value);
  const [confirmDelete, setConfirmDelete] = useState(false);

  function handleSave() {
    onEdit(item.id, editValue);
    setEditing(false);
  }

  function handleCancel() {
    setEditValue(item.value);
    setEditing(false);
  }

  function handleDelete() {
    if (confirmDelete) {
      onDelete(item.id);
      setConfirmDelete(false);
    } else {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 3000);
    }
  }

  return (
    <div className="group flex items-start gap-3 rounded-lg border border-codey-border/50 bg-codey-bg px-4 py-3 transition-colors hover:border-codey-border">
      <div className="flex-1">
        <p className="text-xs font-medium text-codey-text-muted">{item.key}</p>
        {editing ? (
          <div className="mt-1.5">
            <textarea
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-codey-border bg-codey-card px-3 py-2 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            />
            <div className="mt-2 flex gap-2">
              <button
                onClick={handleSave}
                className="flex items-center gap-1.5 rounded-lg bg-codey-green px-3 py-1.5 text-xs font-semibold text-codey-bg hover:shadow-glow-green"
              >
                <Check className="h-3 w-3" />
                Save
              </button>
              <button
                onClick={handleCancel}
                className="flex items-center gap-1.5 rounded-lg border border-codey-border px-3 py-1.5 text-xs text-codey-text-dim hover:bg-codey-card-hover"
              >
                <X className="h-3 w-3" />
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <p className="mt-0.5 text-sm text-codey-text">{item.value}</p>
        )}
        <p className="mt-1 text-xs text-codey-text-muted">
          Updated {formatTimestamp(item.updated_at)}
        </p>
      </div>
      {!editing && (
        <div className="flex shrink-0 gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            onClick={() => setEditing(true)}
            className="rounded-lg p-1.5 text-codey-text-muted hover:bg-codey-card-hover hover:text-codey-green"
            title="Edit"
          >
            <Edit3 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handleDelete}
            className={`rounded-lg p-1.5 transition-colors ${
              confirmDelete
                ? "bg-codey-red/20 text-codey-red"
                : "text-codey-text-muted hover:bg-codey-card-hover hover:text-codey-red"
            }`}
            title={confirmDelete ? "Click again to confirm" : "Delete"}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function MemoryViewerPage() {
  const { user } = useAuth();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [timeline, setTimeline] = useState<MemoryUpdate[]>([]);
  const [loading, setLoading] = useState(true);
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [resetInput, setResetInput] = useState("");
  const [showReset, setShowReset] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const [memData, timelineData] = await Promise.all([
          api.get<MemoryItem[]>("/memory"),
          api.get<MemoryUpdate[]>("/memory/timeline?limit=20"),
        ]);
        setMemories(memData);
        setTimeline(timelineData);
      } catch (err) {
        console.error("Failed to load memory:", err);
        // Use demo data for initial display
        setMemories([
          { id: "1", dimension: "language_preferences", key: "Primary Language", value: "Codey knows you prefer Python and TypeScript", updated_at: new Date().toISOString() },
          { id: "2", dimension: "coding_style", key: "Naming Convention", value: "You use snake_case in Python and camelCase in TypeScript", updated_at: new Date().toISOString() },
          { id: "3", dimension: "coding_style", key: "Comments", value: "You write minimal comments, preferring self-documenting code", updated_at: new Date().toISOString() },
          { id: "4", dimension: "communication", key: "Response Style", value: "You prefer concise explanations with code examples", updated_at: new Date().toISOString() },
          { id: "5", dimension: "project_context", key: "Active Project", value: "Working on a SaaS platform with Next.js frontend and FastAPI backend", updated_at: new Date().toISOString() },
          { id: "6", dimension: "workflow", key: "Git Flow", value: "You use feature branches with squash merges", updated_at: new Date().toISOString() },
          { id: "7", dimension: "personal", key: "Editor", value: "VS Code with Vim keybindings", updated_at: new Date().toISOString() },
        ]);
        setTimeline([]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  function getMemoriesForDimension(dimensionKey: string): MemoryItem[] {
    return memories.filter((m) => m.dimension === dimensionKey);
  }

  async function handleEditMemory(id: string, value: string) {
    try {
      await api.patch(`/memory/${id}`, { value });
      setMemories((prev) =>
        prev.map((m) =>
          m.id === id ? { ...m, value, updated_at: new Date().toISOString() } : m
        )
      );
    } catch {
      // Optimistic update fallback
      setMemories((prev) =>
        prev.map((m) =>
          m.id === id ? { ...m, value, updated_at: new Date().toISOString() } : m
        )
      );
    }
  }

  async function handleDeleteMemory(id: string) {
    try {
      await api.delete(`/memory/${id}`);
    } catch {}
    setMemories((prev) => prev.filter((m) => m.id !== id));
  }

  async function handleAddMemory(dimension: string) {
    if (!newKey.trim() || !newValue.trim()) return;
    const newItem: MemoryItem = {
      id: Math.random().toString(36).slice(2),
      dimension,
      key: newKey.trim(),
      value: newValue.trim(),
      updated_at: new Date().toISOString(),
    };

    try {
      const created = await api.post<MemoryItem>("/memory", {
        dimension,
        key: newKey.trim(),
        value: newValue.trim(),
      });
      setMemories((prev) => [...prev, created]);
    } catch {
      setMemories((prev) => [...prev, newItem]);
    }

    setNewKey("");
    setNewValue("");
    setAddingTo(null);
  }

  function handleExportJSON() {
    const data = DIMENSIONS.map((dim) => ({
      dimension: dim.label,
      items: getMemoriesForDimension(dim.key).map((m) => ({
        key: m.key,
        value: m.value,
        updated_at: m.updated_at,
      })),
    }));
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `codey-memory-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleResetMemory() {
    if (resetInput !== "RESET MEMORY") return;
    setResetting(true);
    try {
      await api.delete("/memory/all");
    } catch {}
    setMemories([]);
    setTimeline([]);
    setResetInput("");
    setShowReset(false);
    setResetting(false);
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-codey-text">
            <Brain className="h-6 w-6 text-codey-green" />
            Memory
          </h1>
          <p className="mt-1 text-sm text-codey-text-dim">
            Everything Codey has learned about how you work. Edit, add, or remove items at any time.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleExportJSON}
            className="flex items-center gap-1.5 rounded-lg border border-codey-border px-3 py-2 text-xs font-medium text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
          >
            <Download className="h-3.5 w-3.5" />
            Export JSON
          </button>
          <button
            onClick={() => setShowReset(true)}
            className="flex items-center gap-1.5 rounded-lg border border-codey-red/30 px-3 py-2 text-xs font-medium text-codey-red transition-colors hover:bg-codey-red-glow"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Reset
          </button>
        </div>
      </div>

      {/* Reset Confirmation */}
      {showReset && (
        <div className="rounded-xl border border-codey-red/30 bg-codey-red-glow p-5">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-codey-red" />
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-codey-red">Reset All Memory</h3>
              <p className="mt-1 text-sm text-codey-text-dim">
                This will permanently delete everything Codey has learned about you. Type{" "}
                <code className="rounded bg-codey-bg px-1.5 py-0.5 font-mono text-xs text-codey-red">
                  RESET MEMORY
                </code>{" "}
                to confirm.
              </p>
              <input
                type="text"
                value={resetInput}
                onChange={(e) => setResetInput(e.target.value)}
                placeholder="RESET MEMORY"
                className="mt-3 w-full rounded-lg border border-codey-red/30 bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-red focus:outline-none"
              />
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => {
                    setShowReset(false);
                    setResetInput("");
                  }}
                  className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover"
                >
                  Cancel
                </button>
                <button
                  onClick={handleResetMemory}
                  disabled={resetInput !== "RESET MEMORY" || resetting}
                  className="flex items-center gap-2 rounded-lg bg-codey-red px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {resetting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                  Reset all memory
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Memory Dimensions */}
      {DIMENSIONS.map((dim) => {
        const items = getMemoriesForDimension(dim.key);
        const Icon = dim.icon;
        return (
          <div
            key={dim.key}
            className="rounded-xl border border-codey-border bg-codey-card"
          >
            <div className="flex items-center justify-between border-b border-codey-border/50 px-5 py-4">
              <div className="flex items-center gap-2">
                <Icon className="h-4 w-4 text-codey-text-dim" />
                <div>
                  <h2 className="text-sm font-semibold text-codey-text">{dim.label}</h2>
                  <p className="text-xs text-codey-text-muted">{dim.description}</p>
                </div>
              </div>
              <button
                onClick={() => setAddingTo(addingTo === dim.key ? null : dim.key)}
                className="flex items-center gap-1 rounded-lg border border-codey-border px-2.5 py-1.5 text-xs text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-green"
              >
                <Plus className="h-3 w-3" />
                Add
              </button>
            </div>

            <div className="px-5 py-4">
              {/* Add form */}
              {addingTo === dim.key && (
                <div className="mb-4 space-y-2 rounded-lg border border-codey-green/30 bg-codey-green-glow p-4">
                  <input
                    type="text"
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    placeholder="Label (e.g., Testing Framework)"
                    className="w-full rounded-lg border border-codey-border bg-codey-bg px-3 py-2 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                  />
                  <textarea
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    placeholder="What should Codey remember? (e.g., You prefer pytest with fixtures over unittest)"
                    rows={2}
                    className="w-full rounded-lg border border-codey-border bg-codey-bg px-3 py-2 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleAddMemory(dim.key)}
                      disabled={!newKey.trim() || !newValue.trim()}
                      className="flex items-center gap-1.5 rounded-lg bg-codey-green px-3 py-1.5 text-xs font-semibold text-codey-bg hover:shadow-glow-green disabled:opacity-50"
                    >
                      <Plus className="h-3 w-3" />
                      Add memory
                    </button>
                    <button
                      onClick={() => {
                        setAddingTo(null);
                        setNewKey("");
                        setNewValue("");
                      }}
                      className="rounded-lg border border-codey-border px-3 py-1.5 text-xs text-codey-text-dim hover:bg-codey-card-hover"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* Items */}
              {items.length === 0 ? (
                <p className="py-4 text-center text-sm text-codey-text-muted">
                  No memories yet in this category. Codey will learn as you use it, or add your own.
                </p>
              ) : (
                <div className="space-y-2">
                  {items.map((item) => (
                    <MemoryItemRow
                      key={item.id}
                      item={item}
                      onEdit={handleEditMemory}
                      onDelete={handleDeleteMemory}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}

      {/* Memory Update Timeline */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border/50 px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-codey-text">
            <Clock className="h-4 w-4 text-codey-text-dim" />
            Memory Update Timeline
          </h2>
        </div>
        <div className="px-5 py-4">
          {timeline.length === 0 ? (
            <p className="py-6 text-center text-sm text-codey-text-muted">
              Memory updates will appear here as Codey learns from your sessions.
            </p>
          ) : (
            <div className="space-y-3">
              {timeline.map((update) => (
                <div key={update.id} className="flex items-start gap-3">
                  <div
                    className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                      update.action === "added"
                        ? "bg-codey-green"
                        : update.action === "updated"
                          ? "bg-codey-yellow"
                          : "bg-codey-red"
                    }`}
                  />
                  <div className="flex-1">
                    <p className="text-sm text-codey-text">
                      <span className="font-medium capitalize">{update.action}</span>{" "}
                      <span className="text-codey-text-dim">{update.key}</span>
                    </p>
                    <p className="mt-0.5 text-xs text-codey-text-muted">
                      {update.value}
                    </p>
                    <p className="mt-0.5 text-xs text-codey-text-muted">
                      {formatTimestamp(update.timestamp)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
