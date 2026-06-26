"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { KeyRound, Loader2, Check, Trash2, AlertTriangle, Cpu } from "lucide-react";
import DashboardLayout from "@/components/layout/DashboardLayout";
import { ProtectedRoute } from "@/lib/auth";
import { useToast } from "@/components/ui/ToastProvider";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  groq: "Groq",
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  together: "Together AI",
  fireworks: "Fireworks AI",
  cerebras: "Cerebras",
  mistral: "Mistral",
  gemini: "Google Gemini",
};

interface ByokStatus {
  configured: boolean;
  provider: string | null;
  model: string | null;
  has_key: boolean;
  allowed_providers: string[];
}

export default function LlmSettingsPage() {
  const { addToast } = useToast();
  const [status, setStatus] = useState<ByokStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [provider, setProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    try {
      const s = await api.getByok();
      setStatus(s);
      if (s.provider) setProvider(s.provider);
      if (s.model) setModel(s.model);
    } catch (e) {
      setError((e as { detail?: string })?.detail || "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleSave() {
    setError(null);
    setSaved(false);
    if (!apiKey.trim()) {
      setError("Enter your API key.");
      return;
    }
    setSaving(true);
    try {
      await api.setByok(provider, apiKey.trim(), model.trim() || undefined);
      setApiKey("");
      setSaved(true);
      await load();
      addToast("Key saved — it will be validated on first use", "success");
    } catch (e) {
      const msg = (e as { detail?: string })?.detail || "Failed to save";
      setError(msg);
      addToast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.clearByok();
      setApiKey("");
      setModel("");
      await load();
      addToast("Key removed", "success");
    } catch (e) {
      const msg = (e as { detail?: string })?.detail || "Failed to clear";
      setError(msg);
      addToast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  const providers = status?.allowed_providers?.length
    ? status.allowed_providers
    : Object.keys(PROVIDER_LABELS);

  return (
    <ProtectedRoute>
      <DashboardLayout>
        <div className="mx-auto max-w-3xl space-y-6">
      <div className="mb-6 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-codey-green/10 text-codey-green">
          <Cpu className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-codey-text">Bring your own API key</h1>
          <p className="text-sm text-codey-text-muted">
            Use your own LLM provider key for generation. Stored encrypted; used only for your account.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-codey-text-muted">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : (
        <div className="space-y-5 rounded-xl border border-codey-border bg-codey-card p-6">
          {status?.configured && (
            <div className="flex items-center gap-2 rounded-lg border border-codey-green/30 bg-codey-green/10 px-3 py-2 text-sm text-codey-green">
              <Check className="h-4 w-4" />
              Active: {PROVIDER_LABELS[status.provider || ""] || status.provider}
              {status.model ? ` · ${status.model}` : ""}
            </div>
          )}

          <div>
            <label className="mb-1.5 block text-xs font-medium text-codey-text-muted">Provider</label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="w-full rounded-lg border border-codey-border bg-codey-bg px-3 py-2 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            >
              {providers.map((p) => (
                <option key={p} value={p}>
                  {PROVIDER_LABELS[p] || p}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-codey-text-muted">
              API key {status?.has_key ? "(leave blank to keep current)" : ""}
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={status?.has_key ? "•••••••••• (stored)" : "sk-…"}
              autoComplete="off"
              className="w-full rounded-lg border border-codey-border bg-codey-bg px-3 py-2 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-codey-text-muted">
              Model <span className="text-codey-text-muted">(optional — uses a sensible default)</span>
            </label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. gpt-4o, llama-3.3-70b-versatile"
              className="w-full rounded-lg border border-codey-border bg-codey-bg px-3 py-2 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            />
            <p className="mt-1.5 text-xs text-codey-text-muted">
              Common models — OpenAI: gpt-4o, gpt-4o-mini · Groq: llama-3.3-70b-versatile ·
              DeepSeek: deepseek-chat · Mistral: mistral-large-latest · Gemini: gemini-1.5-pro.
              Leave blank to use the provider default.
            </p>
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
              <AlertTriangle className="h-4 w-4" /> {error}
            </div>
          )}
          {saved && !error && (
            <div className="text-sm text-codey-green">
              Saved. Your key is validated on first use — if it is invalid, generation will report an error.
            </div>
          )}

          <div className="flex gap-3">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg hover:shadow-glow-green disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
              Save key
            </button>
            {status?.configured && (
              <button
                onClick={handleClear}
                disabled={saving}
                className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover disabled:opacity-50"
              >
                <Trash2 className="h-4 w-4" /> Remove
              </button>
            )}
          </div>

          <p className="text-xs text-codey-text-muted">
            Supported: OpenAI, Groq, OpenRouter, DeepSeek, Together, Fireworks, Cerebras, Mistral, Gemini.
            Your key is encrypted at rest and never shown again.
          </p>
        </div>
      )}
        </div>
      </DashboardLayout>
    </ProtectedRoute>
  );
}
