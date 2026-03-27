"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  Sparkles,
  Code,
  Play,
  CheckCircle2,
  Zap,
  ArrowRight,
  ArrowLeft,
  Terminal,
  Loader2,
  Copy,
  Check,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Step {
  number: number;
  title: string;
  description: string;
}

const STEPS: Step[] = [
  { number: 1, title: "Welcome to Codey", description: "Your AI coding engine" },
  { number: 2, title: "Your First Prompt", description: "Tell Codey what to build" },
  { number: 3, title: "Watch It Work", description: "Streaming code generation" },
  { number: 4, title: "Your Result", description: "Production-ready code" },
  { number: 5, title: "Go Further", description: "Unlock the full experience" },
];

const DEMO_PROMPT = "Build a REST API endpoint in FastAPI that accepts a JSON body with a list of URLs, fetches each one concurrently, and returns the HTTP status code for each.";

const DEMO_OUTPUT_LINES = [
  'from fastapi import FastAPI',
  'from pydantic import BaseModel',
  'import httpx',
  'import asyncio',
  '',
  'app = FastAPI()',
  '',
  'class UrlCheckRequest(BaseModel):',
  '    urls: list[str]',
  '',
  'class UrlCheckResult(BaseModel):',
  '    url: str',
  '    status_code: int | None',
  '    error: str | None = None',
  '',
  '@app.post("/check-urls")',
  'async def check_urls(request: UrlCheckRequest):',
  '    async with httpx.AsyncClient(timeout=10.0) as client:',
  '        tasks = [fetch_status(client, url) for url in request.urls]',
  '        results = await asyncio.gather(*tasks)',
  '    return {"results": results}',
  '',
  'async def fetch_status(client: httpx.AsyncClient, url: str):',
  '    try:',
  '        response = await client.get(url)',
  '        return UrlCheckResult(url=url, status_code=response.status_code)',
  '    except httpx.RequestError as e:',
  '        return UrlCheckResult(url=url, status_code=None, error=str(e))',
];

// ── Progress Dots ─────────────────────────────────────────────────────────────

function ProgressDots({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-2">
      {Array.from({ length: total }, (_, i) => (
        <div
          key={i}
          className={`h-2 rounded-full transition-all ${
            i + 1 === current
              ? "w-8 bg-codey-green"
              : i + 1 < current
                ? "w-2 bg-codey-green/50"
                : "w-2 bg-codey-border"
          }`}
        />
      ))}
    </div>
  );
}

// ── Typing Animation ──────────────────────────────────────────────────────────

function StreamingCode({ lines, onComplete }: { lines: string[]; onComplete: () => void }) {
  const [visibleLines, setVisibleLines] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (visibleLines < lines.length) {
      const timer = setTimeout(() => {
        setVisibleLines((prev) => prev + 1);
        if (containerRef.current) {
          containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
      }, 80);
      return () => clearTimeout(timer);
    } else {
      const timer = setTimeout(onComplete, 500);
      return () => clearTimeout(timer);
    }
  }, [visibleLines, lines.length, onComplete]);

  return (
    <div
      ref={containerRef}
      className="h-72 overflow-y-auto rounded-lg border border-codey-border bg-codey-bg font-mono text-sm"
    >
      <div className="flex items-center gap-2 border-b border-codey-border/50 px-4 py-2">
        <div className="h-2.5 w-2.5 rounded-full bg-codey-red/60" />
        <div className="h-2.5 w-2.5 rounded-full bg-codey-yellow/60" />
        <div className="h-2.5 w-2.5 rounded-full bg-codey-green/60" />
        <span className="ml-2 text-xs text-codey-text-muted">main.py</span>
      </div>
      <div className="p-4">
        {lines.slice(0, visibleLines).map((line, i) => (
          <div key={i} className="flex">
            <span className="mr-4 inline-block w-6 text-right text-codey-text-muted select-none">
              {i + 1}
            </span>
            <span className="text-codey-text">{line}</span>
          </div>
        ))}
        {visibleLines < lines.length && (
          <div className="flex items-center">
            <span className="mr-4 inline-block w-6 text-right text-codey-text-muted select-none">
              {visibleLines + 1}
            </span>
            <span className="inline-block h-4 w-2 animate-pulse bg-codey-green" />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function OnboardingPage() {
  const router = useRouter();
  const { user } = useAuth();
  const [step, setStep] = useState(1);
  const [prompt, setPrompt] = useState(DEMO_PROMPT);
  const [streamComplete, setStreamComplete] = useState(false);
  const [copied, setCopied] = useState(false);

  // Animated typing for welcome
  const [welcomeText, setWelcomeText] = useState("");
  const fullWelcome = "Hi, I'm Codey. I write production code from plain English.";

  useEffect(() => {
    if (step === 1 && welcomeText.length < fullWelcome.length) {
      const timer = setTimeout(() => {
        setWelcomeText(fullWelcome.slice(0, welcomeText.length + 1));
      }, 35);
      return () => clearTimeout(timer);
    }
  }, [step, welcomeText, fullWelcome]);

  function handleCopyCode() {
    navigator.clipboard.writeText(DEMO_OUTPUT_LINES.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function nextStep() {
    if (step < 5) setStep(step + 1);
  }

  function prevStep() {
    if (step > 1) setStep(step - 1);
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-codey-bg px-4">
      <div className="w-full max-w-2xl">
        {/* Progress Dots */}
        <div className="mb-8 flex justify-center">
          <ProgressDots current={step} total={5} />
        </div>

        {/* Step Content */}
        <div className="animate-fade-in rounded-2xl border border-codey-border bg-codey-card p-8 shadow-2xl">
          {/* ── Step 1: Welcome ─────────────────────────────────────────── */}
          {step === 1 && (
            <div className="text-center">
              <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-2xl bg-codey-green/10">
                <Sparkles className="h-10 w-10 text-codey-green" />
              </div>
              <div className="mt-6 min-h-[3rem]">
                <h1 className="text-2xl font-bold text-codey-text">
                  {welcomeText}
                  {welcomeText.length < fullWelcome.length && (
                    <span className="inline-block h-6 w-0.5 animate-pulse bg-codey-green ml-0.5" />
                  )}
                </h1>
              </div>
              <p className="mt-4 text-sm text-codey-text-dim">
                Describe what you need in plain English. Codey generates production-ready code,
                analyzes structural health, and learns how you work over time.
              </p>
              <div className="mt-8 grid grid-cols-3 gap-4 text-center">
                {[
                  { icon: Code, label: "Generate Code", desc: "From prompts" },
                  { icon: Terminal, label: "Health Analysis", desc: "Structural health" },
                  { icon: Sparkles, label: "Learns You", desc: "Adapts over time" },
                ].map(({ icon: Icon, label, desc }) => (
                  <div key={label} className="rounded-lg bg-codey-bg p-3">
                    <Icon className="mx-auto h-5 w-5 text-codey-green" />
                    <p className="mt-2 text-xs font-medium text-codey-text">{label}</p>
                    <p className="text-xs text-codey-text-muted">{desc}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Step 2: First Prompt ────────────────────────────────────── */}
          {step === 2 && (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-codey-green/10">
                  <Code className="h-5 w-5 text-codey-green" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-codey-text">Write Your First Prompt</h2>
                  <p className="text-sm text-codey-text-dim">
                    Try editing this, or use it as-is.
                  </p>
                </div>
              </div>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={4}
                className="mt-5 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-3 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
              />
              <p className="mt-2 text-xs text-codey-text-muted">
                Pro tip: Be specific about the language, framework, and behavior you want.
              </p>
            </div>
          )}

          {/* ── Step 3: Watch Streaming ─────────────────────────────────── */}
          {step === 3 && (
            <div>
              <div className="mb-4 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-codey-green/10">
                  <Play className="h-5 w-5 text-codey-green" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-codey-text">Watch Codey Work</h2>
                  <p className="text-sm text-codey-text-dim">
                    Code streams in real-time as Codey generates it.
                  </p>
                </div>
              </div>
              <StreamingCode
                lines={DEMO_OUTPUT_LINES}
                onComplete={() => setStreamComplete(true)}
              />
              {!streamComplete && (
                <div className="mt-3 flex items-center gap-2 text-xs text-codey-yellow">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Generating...
                </div>
              )}
              {streamComplete && (
                <div className="mt-3 flex items-center gap-2 text-xs text-codey-green">
                  <CheckCircle2 className="h-3 w-3" />
                  Generation complete &mdash; 27 lines, Health: Healthy
                </div>
              )}
            </div>
          )}

          {/* ── Step 4: Result + Next Action ────────────────────────────── */}
          {step === 4 && (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-codey-green/10">
                  <CheckCircle2 className="h-5 w-5 text-codey-green" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-codey-text">Your Code is Ready</h2>
                  <p className="text-sm text-codey-text-dim">
                    Production-quality, structurally analyzed, saved to your vault.
                  </p>
                </div>
              </div>

              <div className="mt-5 space-y-3">
                <div className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-bg p-4">
                  <div>
                    <p className="text-sm font-medium text-codey-text">Health Score</p>
                    <p className="text-xs text-codey-text-dim">Structural health analysis</p>
                  </div>
                  <span className="inline-flex items-center rounded-full bg-codey-green/20 px-3 py-1 text-sm font-bold text-codey-green">
                    Healthy (0.85)
                  </span>
                </div>

                <div className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-bg p-4">
                  <div>
                    <p className="text-sm font-medium text-codey-text">Credits Used</p>
                    <p className="text-xs text-codey-text-dim">From your free allocation</p>
                  </div>
                  <span className="text-sm font-bold text-codey-text">12 credits</span>
                </div>

                <div className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-bg p-4">
                  <div>
                    <p className="text-sm font-medium text-codey-text">Saved to Vault</p>
                    <p className="text-xs text-codey-text-dim">Version 1 archived</p>
                  </div>
                  <CheckCircle2 className="h-5 w-5 text-codey-green" />
                </div>
              </div>

              <button
                onClick={handleCopyCode}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
              >
                {copied ? (
                  <>
                    <Check className="h-4 w-4 text-codey-green" />
                    Copied!
                  </>
                ) : (
                  <>
                    <Copy className="h-4 w-4" />
                    Copy generated code
                  </>
                )}
              </button>
            </div>
          )}

          {/* ── Step 5: Upgrade Prompt ──────────────────────────────────── */}
          {step === 5 && (
            <div className="text-center">
              <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-2xl bg-codey-green/10">
                <Zap className="h-10 w-10 text-codey-green" />
              </div>
              <h2 className="mt-6 text-2xl font-bold text-codey-text">
                You&apos;re Ready to Build
              </h2>
              <p className="mt-3 text-sm text-codey-text-dim">
                You have {user?.credits_remaining ?? 500} credits to start. Upgrade to Pro for
                5,000 credits/month, autonomous mode, and GitHub integration.
              </p>

              <div className="mt-8 space-y-3">
                <button
                  onClick={() => router.push("/dashboard/prompt")}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-codey-green px-6 py-3 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green"
                >
                  <Code className="h-4 w-4" />
                  Start Building
                </button>
                <button
                  onClick={() => router.push("/pricing")}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-codey-border px-6 py-3 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
                >
                  <Zap className="h-4 w-4" />
                  View Pro Plans
                </button>
                <button
                  onClick={() => router.push("/dashboard")}
                  className="text-xs text-codey-text-muted hover:text-codey-text-dim"
                >
                  Skip to Dashboard
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Navigation Buttons */}
        <div className="mt-6 flex items-center justify-between">
          <button
            onClick={prevStep}
            disabled={step === 1}
            className="flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm text-codey-text-dim transition-colors hover:text-codey-text disabled:invisible"
          >
            <ArrowLeft className="h-4 w-4" />
            Back
          </button>

          <div className="text-xs text-codey-text-muted">
            {step} of {STEPS.length}
          </div>

          {step < 5 ? (
            <button
              onClick={nextStep}
              className="flex items-center gap-1.5 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green"
            >
              {step === 3 && !streamComplete ? "Skip" : "Next"}
              <ArrowRight className="h-4 w-4" />
            </button>
          ) : (
            <div className="w-20" />
          )}
        </div>
      </div>
    </div>
  );
}
