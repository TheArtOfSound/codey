"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
  Network,
  Shield,
  Bot,
  Check,
  Minus,
  ChevronDown,
  Zap,
  ArrowRight,
} from "lucide-react";

// ── Animated Background ──────────────────────────────────────────────────────

function GridBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* Dot grid */}
      <div
        className="absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "radial-gradient(circle, #00ff88 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />
      {/* Horizontal scan line */}
      <div
        className="absolute left-0 right-0 h-px opacity-20"
        style={{
          background:
            "linear-gradient(90deg, transparent, #00ff88, transparent)",
          animation: "scanV 6s ease-in-out infinite",
        }}
      />
      {/* Radial glow */}
      <div className="absolute left-1/2 top-1/3 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-codey-green/5 blur-[120px]" />
      <style jsx>{`
        @keyframes scanV {
          0%,
          100% {
            top: 10%;
          }
          50% {
            top: 50%;
          }
        }
      `}</style>
    </div>
  );
}

// ── Fake Terminal Demo ───────────────────────────────────────────────────────

function LiveDemo() {
  const [lineIndex, setLineIndex] = useState(0);

  const lines = [
    { text: "$ codey analyze ./src", cls: "text-codey-text-dim" },
    { text: "Scanning 847 files across 23 packages...", cls: "text-codey-text-dim" },
    { text: "Building dependency graph ██████████ 100%", cls: "text-codey-green" },
    { text: "", cls: "" },
    {
      text: "  Structural Report",
      cls: "text-codey-text font-bold",
    },
    { text: "  ├─ Nodes: 847   Edges: 3,291", cls: "text-codey-text-dim" },
    { text: "  ├─ Coupling:   0.82  ── coupling health", cls: "text-codey-green" },
    { text: "  ├─ Stability:  0.14  ── stress level", cls: "text-codey-green" },
    { text: "  ├─ Phase:      Laminar Flow ✓", cls: "text-codey-green" },
    { text: "", cls: "" },
    {
      text: "  ⚠  2 collapse-risk clusters detected:",
      cls: "text-codey-yellow",
    },
    {
      text: '    → src/api/handlers.ts  (fan-in: 34, local stability: 0.71)',
      cls: "text-codey-yellow",
    },
    {
      text: '    → src/db/queries.ts    (fan-in: 28, local stability: 0.63)',
      cls: "text-codey-yellow",
    },
    { text: "", cls: "" },
    {
      text: "  Generating refactor plan to improve stability...",
      cls: "text-codey-text-dim",
    },
    {
      text: "  ✓ Plan ready — 4 files, estimated 12 credits",
      cls: "text-codey-green",
    },
  ];

  useEffect(() => {
    if (lineIndex >= lines.length) return;
    const delay = lineIndex === 0 ? 800 : lineIndex < 3 ? 600 : 300;
    const timer = setTimeout(() => setLineIndex((i) => i + 1), delay);
    return () => clearTimeout(timer);
  }, [lineIndex, lines.length]);

  return (
    <div className="mx-auto max-w-3xl">
      <div className="overflow-hidden rounded-xl border border-codey-border bg-[#0a0a0f] shadow-2xl">
        {/* Title bar */}
        <div className="flex items-center gap-2 border-b border-codey-border px-4 py-3">
          <span className="h-3 w-3 rounded-full bg-codey-red/80" />
          <span className="h-3 w-3 rounded-full bg-codey-yellow/80" />
          <span className="h-3 w-3 rounded-full bg-codey-green/80" />
          <span className="ml-3 text-xs text-codey-text-muted font-mono">
            codey — terminal
          </span>
        </div>
        {/* Lines */}
        <div className="min-h-[360px] p-5 font-mono text-sm leading-relaxed">
          {lines.slice(0, lineIndex).map((line, i) => (
            <div
              key={i}
              className={`${line.cls} animate-fade-in`}
              style={{ animationDelay: `${i * 50}ms` }}
            >
              {line.text || "\u00A0"}
            </div>
          ))}
          {lineIndex < lines.length && (
            <span className="inline-block h-4 w-2 animate-pulse bg-codey-green/80" />
          )}
        </div>
        {/* Health gauges bar */}
        <div className="flex items-center justify-between border-t border-codey-border px-5 py-3">
          <div className="flex items-center gap-6 text-xs">
            <GaugePill label="Coupling" value={0.82} color="green" />
            <GaugePill label="Stability" value={0.14} color="green" />
          </div>
          <span className="badge-green text-xs">Laminar Flow</span>
        </div>
      </div>
    </div>
  );
}

function GaugePill({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: "green" | "yellow" | "red";
}) {
  const barColor =
    color === "green"
      ? "bg-codey-green"
      : color === "yellow"
      ? "bg-codey-yellow"
      : "bg-codey-red";
  return (
    <div className="flex items-center gap-2">
      <span className="text-codey-text-muted">{label}</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-codey-border">
        <div
          className={`h-full ${barColor} transition-all duration-1000`}
          style={{ width: `${value * 100}%` }}
        />
      </div>
      <span className="text-codey-text-dim">{value.toFixed(2)}</span>
    </div>
  );
}

// ── Feature Cards ────────────────────────────────────────────────────────────

const features = [
  {
    icon: Network,
    title: "Network-Aware Generation",
    desc: "Codey maps your entire codebase as a dependency network before writing a single line. Every suggestion respects coupling boundaries, import chains, and data flow paths.",
  },
  {
    icon: Shield,
    title: "Collapse Prediction",
    desc: "Structural stress analysis identifies modules on the edge of cascading failure. Codey warns you before a refactor triggers a chain reaction across your system.",
  },
  {
    icon: Bot,
    title: "Autonomous Mode",
    desc: "Point Codey at a goal and let it work. Multi-step refactors, test generation, and dependency cleanup run autonomously with real-time progress streaming.",
  },
];

// ── Comparison Table ─────────────────────────────────────────────────────────

type CellValue = "check" | "x" | "partial";

interface CompRow {
  feature: string;
  codey: CellValue;
  copilot: CellValue;
  cursor: CellValue;
  claude: CellValue;
}

const compRows: CompRow[] = [
  { feature: "Code generation", codey: "check", copilot: "check", cursor: "check", claude: "check" },
  { feature: "Multi-file editing", codey: "check", copilot: "partial", cursor: "check", claude: "check" },
  { feature: "Dependency graph", codey: "check", copilot: "x", cursor: "x", claude: "x" },
  { feature: "Stress analysis", codey: "check", copilot: "x", cursor: "x", claude: "x" },
  { feature: "Collapse prediction", codey: "check", copilot: "x", cursor: "x", claude: "x" },
  { feature: "Cascade simulation", codey: "check", copilot: "x", cursor: "x", claude: "x" },
  { feature: "Stability optimization", codey: "check", copilot: "x", cursor: "x", claude: "x" },
  { feature: "Autonomous mode", codey: "check", copilot: "x", cursor: "partial", claude: "check" },
  { feature: "Health dashboard", codey: "check", copilot: "x", cursor: "x", claude: "x" },
];

function CellIcon({ value }: { value: CellValue }) {
  if (value === "check")
    return <Check className="mx-auto h-4 w-4 text-codey-green" />;
  if (value === "partial")
    return <Minus className="mx-auto h-4 w-4 text-codey-yellow" />;
  return <Minus className="mx-auto h-4 w-4 text-codey-text-muted/40" />;
}

// ── FAQ ──────────────────────────────────────────────────────────────────────

const faqItems = [
  {
    q: "What is structural health analysis?",
    a: "Codey models your codebase as a dependency network. It quantifies coupling, stress, and cascade risk — metrics no other tool provides.",
  },
  {
    q: "How is Codey different from Copilot?",
    a: "Copilot suggests code line-by-line. Codey understands your entire dependency graph and generates code that improves your system's structural health.",
  },
  {
    q: "What languages are supported?",
    a: "TypeScript, JavaScript, Python, Go, Rust, Java, and C#. More languages are added regularly.",
  },
];

function FAQ() {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="mx-auto max-w-2xl space-y-3">
      {faqItems.map((item, i) => (
        <div key={i} className="rounded-lg border border-codey-border bg-codey-card">
          <button
            onClick={() => setOpen(open === i ? null : i)}
            className="flex w-full items-center justify-between px-5 py-4 text-left text-sm font-medium text-codey-text"
          >
            {item.q}
            <ChevronDown
              className={`h-4 w-4 text-codey-text-dim transition-transform ${
                open === i ? "rotate-180" : ""
              }`}
            />
          </button>
          {open === i && (
            <div className="px-5 pb-4 text-sm leading-relaxed text-codey-text-dim animate-fade-in">
              {item.a}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div className="relative min-h-screen bg-codey-bg text-codey-text">
      <GridBackground />

      {/* Nav */}
      <nav className="relative z-10 flex items-center justify-between px-6 py-5 md:px-12">
        <Link href="/" className="text-xl font-bold tracking-tight">
          <span className="text-codey-green">C</span>ODEY
        </Link>
        <div className="flex items-center gap-4">
          <Link
            href="/pricing"
            className="text-sm text-codey-text-dim hover:text-codey-text transition-colors"
          >
            Pricing
          </Link>
          <Link href="/auth/login" className="btn-ghost text-sm">
            Log in
          </Link>
          <Link href="/auth/signup" className="btn-primary text-sm">
            Sign up free
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative z-10 mx-auto max-w-5xl px-6 pb-20 pt-16 text-center md:pt-28">
        <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-codey-border bg-codey-card px-4 py-1.5 text-xs text-codey-text-dim">
          <Zap className="h-3 w-3 text-codey-green" />
          Powered by structural health analysis
        </div>
        <h1 className="mx-auto max-w-4xl text-4xl font-bold leading-tight tracking-tight md:text-6xl">
          The only coding AI that sees your entire codebase{" "}
          <span className="text-gradient-green">as a network.</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-codey-text-dim md:text-xl">
          Codey maps dependencies, predicts cascading failures, and generates
          code that strengthens your system — not just the file you are looking at.
        </p>
        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <Link
            href="/auth/signup"
            className="btn-primary px-8 py-3 text-base shadow-glow-green"
          >
            Start for free — no credit card
            <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            href="/pricing"
            className="btn-secondary px-8 py-3 text-base"
          >
            View pricing
          </Link>
        </div>
      </section>

      {/* Live Demo */}
      <section className="relative z-10 px-6 pb-28">
        <LiveDemo />
      </section>

      {/* Features */}
      <section className="relative z-10 mx-auto max-w-5xl px-6 pb-28">
        <h2 className="mb-12 text-center text-3xl font-bold">
          What makes Codey different
        </h2>
        <div className="grid gap-6 md:grid-cols-3">
          {features.map((f) => (
            <div
              key={f.title}
              className="card-hover group flex flex-col items-start gap-4"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-codey-green/10 text-codey-green">
                <f.icon className="h-5 w-5" />
              </div>
              <h3 className="text-lg font-semibold">{f.title}</h3>
              <p className="text-sm leading-relaxed text-codey-text-dim">
                {f.desc}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Comparison */}
      <section className="relative z-10 mx-auto max-w-4xl px-6 pb-28">
        <h2 className="mb-12 text-center text-3xl font-bold">
          Codey vs. the rest
        </h2>
        <div className="overflow-x-auto rounded-xl border border-codey-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-codey-border bg-codey-card text-left">
                <th className="px-5 py-3 font-medium text-codey-text-dim">
                  Feature
                </th>
                <th className="px-5 py-3 text-center font-medium text-codey-green">
                  Codey
                </th>
                <th className="px-5 py-3 text-center font-medium text-codey-text-dim">
                  Copilot
                </th>
                <th className="px-5 py-3 text-center font-medium text-codey-text-dim">
                  Cursor
                </th>
                <th className="px-5 py-3 text-center font-medium text-codey-text-dim">
                  Claude Code
                </th>
              </tr>
            </thead>
            <tbody>
              {compRows.map((row, i) => (
                <tr
                  key={row.feature}
                  className={`border-b border-codey-border/50 ${
                    i % 2 === 0 ? "bg-codey-bg" : "bg-codey-card/40"
                  }`}
                >
                  <td className="px-5 py-3 text-codey-text">{row.feature}</td>
                  <td className="px-5 py-3">
                    <CellIcon value={row.codey} />
                  </td>
                  <td className="px-5 py-3">
                    <CellIcon value={row.copilot} />
                  </td>
                  <td className="px-5 py-3">
                    <CellIcon value={row.cursor} />
                  </td>
                  <td className="px-5 py-3">
                    <CellIcon value={row.claude} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Pricing Preview */}
      <section className="relative z-10 mx-auto max-w-3xl px-6 pb-28 text-center">
        <h2 className="mb-4 text-3xl font-bold">Simple, transparent pricing</h2>
        <p className="mb-8 text-codey-text-dim">
          Start free. Scale when you need to.
        </p>
        <Link href="/pricing" className="btn-primary px-8 py-3 text-base">
          See all plans
          <ArrowRight className="h-4 w-4" />
        </Link>
      </section>

      {/* FAQ */}
      <section className="relative z-10 mx-auto max-w-3xl px-6 pb-28">
        <h2 className="mb-10 text-center text-3xl font-bold">
          Frequently asked questions
        </h2>
        <FAQ />
      </section>

      {/* Footer */}
      <footer className="relative z-10 border-t border-codey-border bg-codey-card/30 px-6 py-12">
        <div className="mx-auto flex max-w-5xl flex-col items-center gap-6 md:flex-row md:justify-between">
          <div>
            <span className="text-lg font-bold tracking-tight">
              <span className="text-codey-green">C</span>ODEY
            </span>
            <p className="mt-1 text-xs text-codey-text-muted">
              Qira LLC
            </p>
          </div>
          <div className="flex gap-6 text-sm text-codey-text-dim">
            <Link href="/pricing" className="hover:text-codey-text transition-colors">
              Pricing
            </Link>
            <Link href="/auth/login" className="hover:text-codey-text transition-colors">
              Log in
            </Link>
            <Link href="/auth/signup" className="hover:text-codey-text transition-colors">
              Sign up
            </Link>
          </div>
          <p className="text-xs text-codey-text-muted">
            &copy; {new Date().getFullYear()} Qira LLC. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}
