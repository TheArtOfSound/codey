"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  GitBranch,
  Minus,
  Search,
  Shield,
  Zap,
} from "lucide-react";
import { repoWorkLanes } from "@/lib/repo-work";

function GridBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage:
            "radial-gradient(circle, rgba(0,255,136,0.9) 1px, transparent 1px)",
          backgroundSize: "42px 42px",
        }}
      />
      <div className="absolute inset-x-0 top-0 h-[45vh] bg-[radial-gradient(circle_at_top,rgba(0,255,136,0.18),transparent_58%)]" />
      <div className="absolute -left-24 top-28 h-72 w-72 rounded-full bg-codey-green/10 blur-[120px]" />
      <div className="absolute right-0 top-1/2 h-80 w-80 rounded-full bg-codey-green/5 blur-[140px]" />
    </div>
  );
}

const liveLoop = [
  {
    label: "Dependency drift detected",
    repo: "checkout-service",
    detail: "2 packages passed policy window · PR queued",
    status: "Queued patch",
  },
  {
    label: "Crash cluster spiking",
    repo: "dashboard-web",
    detail: "Auth callback regression linked to release 248",
    status: "Triaging",
  },
  {
    label: "Structural health dropped",
    repo: "billing-api",
    detail: "Stress radius widened across webhook + ledger modules",
    status: "Preparing fix",
  },
  {
    label: "Maintenance window complete",
    repo: "infra-tooling",
    detail: "Tests passed · changelog and PR summary published",
    status: "Verified",
  },
];

function OpsBoard() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setIndex((current) => (current + 1) % liveLoop.length);
    }, 2200);
    return () => clearInterval(timer);
  }, []);

  const active = liveLoop[index];

  return (
    <div className="overflow-hidden rounded-[28px] border border-codey-border bg-[#0b0d12]/95 shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
      <div className="flex items-center justify-between border-b border-codey-border px-5 py-4">
        <div>
          <p className="text-xs uppercase tracking-[0.28em] text-codey-text-muted">
            Repo Command
          </p>
          <p className="mt-1 text-sm font-medium text-codey-text">
            Autonomous maintenance queue
          </p>
        </div>
        <span className="rounded-full border border-codey-green/30 bg-codey-green/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-codey-green">
          Live
        </span>
      </div>

      <div className="grid gap-6 p-5 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: "Repos watched", value: "26" },
              { label: "Risks open", value: "7" },
              { label: "Patches queued", value: "4" },
              { label: "PRs shipped", value: "91" },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-2xl border border-codey-border/80 bg-codey-card/40 px-4 py-4"
              >
                <p className="text-[11px] uppercase tracking-[0.18em] text-codey-text-muted">
                  {item.label}
                </p>
                <p className="mt-2 text-2xl font-semibold text-codey-text">
                  {item.value}
                </p>
              </div>
            ))}
          </div>

          <div className="rounded-2xl border border-codey-border/80 bg-codey-card/40 p-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-[0.2em] text-codey-text-muted">
                  Active incident
                </p>
                <h3 className="mt-2 text-lg font-semibold text-codey-text">
                  {active.label}
                </h3>
                <p className="mt-2 text-sm text-codey-text-dim">{active.detail}</p>
              </div>
              <div className="rounded-full border border-codey-border bg-codey-bg px-3 py-1 text-xs font-medium text-codey-text-dim">
                {active.status}
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-codey-border/80 bg-codey-card/40 p-4">
          <p className="text-xs uppercase tracking-[0.2em] text-codey-text-muted">
            Operator feed
          </p>
          <div className="mt-4 space-y-3">
            {liveLoop.map((item, itemIndex) => {
              const isActive = itemIndex === index;
              return (
                <div
                  key={`${item.repo}-${item.label}`}
                  className={`rounded-2xl border px-4 py-3 transition-all ${
                    isActive
                      ? "border-codey-green/40 bg-codey-green/10"
                      : "border-codey-border/80 bg-codey-bg/60"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-codey-text">{item.repo}</p>
                    <span
                      className={`h-2.5 w-2.5 rounded-full ${
                        isActive ? "bg-codey-green shadow-glow-green" : "bg-codey-text-muted"
                      }`}
                    />
                  </div>
                  <p className="mt-1 text-xs text-codey-text-dim">{item.label}</p>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

const operatingLoop = [
  {
    icon: Search,
    title: "Scan continuously",
    description:
      "Watch repos for structural regressions, risky diffs, stale dependencies, and recurring failures before they become incidents.",
  },
  {
    icon: Activity,
    title: "Prioritize the queue",
    description:
      "Rank work by blast radius, failing checks, churn, and dependency pressure so the highest-value maintenance runs first.",
  },
  {
    icon: GitBranch,
    title: "Prepare the patch",
    description:
      "Generate targeted changes, branch them cleanly, and collect notes, health deltas, and PR-ready rationale.",
  },
  {
    icon: Shield,
    title: "Prove the change",
    description:
      "Run tests, compare before-and-after health, and keep rollback signals visible so autonomy stays accountable.",
  },
];

type Coverage = "check" | "partial" | "none";

const coverageRows: Array<{
  feature: string;
  codey: Coverage;
  copilots: Coverage;
  ci: Coverage;
  bots: Coverage;
}> = [
  { feature: "Continuous repo scanning", codey: "check", copilots: "none", ci: "partial", bots: "partial" },
  { feature: "Priority-ranked maintenance queue", codey: "check", copilots: "none", ci: "none", bots: "none" },
  { feature: "Autonomous patch preparation", codey: "check", copilots: "partial", ci: "none", bots: "partial" },
  { feature: "CI rescue and build repair", codey: "check", copilots: "partial", ci: "partial", bots: "none" },
  { feature: "Security hardening runs", codey: "check", copilots: "partial", ci: "none", bots: "none" },
  { feature: "Docs, runbook, and changelog updates", codey: "check", copilots: "partial", ci: "none", bots: "none" },
  { feature: "Release blocker and deploy handling", codey: "check", copilots: "partial", ci: "partial", bots: "none" },
  { feature: "Health and blast-radius context", codey: "check", copilots: "none", ci: "none", bots: "none" },
  { feature: "Repo-level run history", codey: "check", copilots: "none", ci: "partial", bots: "partial" },
  { feature: "One operator view across repos", codey: "check", copilots: "none", ci: "none", bots: "none" },
];

function CoverageIcon({ value }: { value: Coverage }) {
  if (value === "check") {
    return <Check className="mx-auto h-4 w-4 text-codey-green" />;
  }
  if (value === "partial") {
    return <Minus className="mx-auto h-4 w-4 text-codey-yellow" />;
  }
  return <Minus className="mx-auto h-4 w-4 text-codey-text-muted/40" />;
}

const faqItems = [
  {
    q: "What does Codey actually manage?",
    a: "Connected repositories, maintenance queues, structural risk, bug repair, dependency drift, CI rescue, security hardening, docs, and release-blocking repo work with reviewable outputs.",
  },
  {
    q: "Is this just another coding copilot?",
    a: "No. Copilots wait for prompts inside an editor. Codey stays on the repo, watches what changes, decides what matters, and prepares work continuously.",
  },
  {
    q: "Does Codey cover more than refactors and dependency bumps?",
    a: "Yes. The operator model spans repo scans, targeted fixes, CI repair, security passes, tests, docs, release blockers, and tooling work. Some lanes run continuously, others are queued on demand.",
  },
  {
    q: "Can I control what autonomy is allowed to do?",
    a: "Yes. Repo-level policies let you gate refactors, bug fixes, dependency updates, and impact thresholds before Codey takes action.",
  },
];

function FAQ() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <div className="mx-auto max-w-3xl divide-y divide-codey-border rounded-[28px] border border-codey-border bg-codey-card/40">
      {faqItems.map((item, index) => (
        <div key={item.q} className="px-5 py-4">
          <button
            onClick={() => setOpen(open === index ? null : index)}
            className="flex w-full items-center justify-between gap-4 text-left"
          >
            <span className="text-base font-medium text-codey-text">{item.q}</span>
            <ChevronDown
              className={`h-4 w-4 text-codey-text-dim transition-transform ${
                open === index ? "rotate-180" : ""
              }`}
            />
          </button>
          {open === index && (
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-codey-text-dim">
              {item.a}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-codey-bg text-codey-text">
      <GridBackground />

      <nav className="relative z-10 flex items-center justify-between px-6 py-5 md:px-12">
        <Link href="/" className="text-xl font-bold tracking-tight">
          <span className="text-codey-green">C</span>ODEY
        </Link>
        <div className="flex items-center gap-4">
          <Link
            href="/pricing"
            className="text-sm text-codey-text-dim transition-colors hover:text-codey-text"
          >
            Pricing
          </Link>
          <Link href="/auth/login" className="btn-ghost text-sm">
            Log in
          </Link>
          <Link href="/auth/signup" className="btn-primary text-sm">
            Start managing repos
          </Link>
        </div>
      </nav>

      <section className="relative z-10 px-6 pb-20 pt-10 md:px-12 md:pb-28 md:pt-16">
        <div className="mx-auto grid max-w-7xl gap-12 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-codey-border bg-codey-card/60 px-4 py-2 text-[11px] uppercase tracking-[0.22em] text-codey-text-dim">
              <Bot className="h-3.5 w-3.5 text-codey-green" />
              Autonomous repository operations
            </div>
            <h1 className="mt-6 text-5xl font-bold leading-[0.95] tracking-tight md:text-7xl">
              Codey runs the
              <span className="block text-gradient-green">maintenance queue</span>
              your repos never finish.
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-codey-text-dim md:text-lg">
              Monitor every repository, surface real risk, queue the highest-value fixes,
              and ship reviewable patches with structural context. Codey covers the
              repo-work loop from scans and CI rescue through security, docs, and release
              blockers. It is an operator for repo health, not a prettier prompt box.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link href="/auth/signup" className="btn-primary px-8 py-3 text-base shadow-glow-green">
                Connect your first repo
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Link href="/dashboard/autonomous" className="btn-secondary px-8 py-3 text-base">
                See autopilot controls
              </Link>
            </div>
            <div className="mt-8 grid max-w-xl grid-cols-2 gap-4 text-sm text-codey-text-dim sm:grid-cols-4">
              {[
                "Recurring scans",
                "CI rescue",
                "Security passes",
                "Release blockers",
              ].map((item) => (
                <div key={item} className="border-t border-codey-border pt-3">
                  {item}
                </div>
              ))}
            </div>
          </div>

          <OpsBoard />
        </div>
      </section>

      <section className="relative z-10 mx-auto max-w-7xl px-6 pb-24 md:px-12">
        <div className="grid gap-6 border-t border-codey-border pt-10 md:grid-cols-4">
          {operatingLoop.map((item) => (
            <div key={item.title} className="pr-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-full border border-codey-border bg-codey-card/60">
                <item.icon className="h-4 w-4 text-codey-green" />
              </div>
              <h2 className="mt-5 text-lg font-semibold text-codey-text">{item.title}</h2>
              <p className="mt-2 text-sm leading-6 text-codey-text-dim">
                {item.description}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="relative z-10 mx-auto max-w-7xl px-6 pb-24 md:px-12">
        <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr]">
          <div>
            <p className="text-xs uppercase tracking-[0.24em] text-codey-text-muted">
              What Codey owns
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight md:text-4xl">
              Built for repository stewardship, not one-off prompts.
            </h2>
            <p className="mt-4 max-w-xl text-sm leading-7 text-codey-text-dim">
              The product is centered on fleets of repos, recurring maintenance, and
              autonomous execution loops. Every surface should make operators faster at
              deciding what to fix next and safer at letting Codey handle the routine work.
            </p>
          </div>
          <div className="grid gap-5 md:grid-cols-2">
            {[
              "Watch dependency drift and aging branches across the fleet.",
              "Surface hotspots using structural stress, blast radius, and failure history.",
              "Queue interventions with repo context, scope, and expected impact already attached.",
              "Run patch preparation, validation, and summaries without babysitting a chat thread.",
            ].map((line) => (
              <div key={line} className="border-t border-codey-border pt-4 text-sm leading-6 text-codey-text">
                {line}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="relative z-10 mx-auto max-w-7xl px-6 pb-24 md:px-12">
        <div className="overflow-hidden rounded-[28px] border border-codey-border bg-codey-card/40">
          <div className="border-b border-codey-border px-6 py-5">
            <p className="text-xs uppercase tracking-[0.24em] text-codey-text-muted">
              Full Repo-Work Coverage
            </p>
            <h2 className="mt-2 text-2xl font-bold">
              Every lane in the repo lifecycle has an operator path.
            </h2>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-codey-text-dim">
              Codey is designed to cover the same repo work an elite maintenance operator
              handles manually: scan, repair, refactor, verify, secure, document, and ship.
              Continuous lanes stay watched. The rest are queued as focused runs.
            </p>
          </div>
          <div className="divide-y divide-codey-border/60">
            {repoWorkLanes.map((lane) => (
              <div
                key={lane.id}
                className="grid gap-4 px-6 py-5 md:grid-cols-[0.9fr_1.2fr_1fr_0.7fr]"
              >
                <div>
                  <p className="text-sm font-semibold text-codey-text">{lane.label}</p>
                  <p className="mt-1 text-xs uppercase tracking-[0.18em] text-codey-text-muted">
                    {lane.modeLabel}
                  </p>
                </div>
                <p className="text-sm leading-6 text-codey-text-dim">{lane.summary}</p>
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-codey-text-muted">
                    Signals
                  </p>
                  <p className="mt-2 text-sm leading-6 text-codey-text">
                    {lane.signals}
                  </p>
                </div>
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-codey-text-muted">
                    Returns
                  </p>
                  <p className="mt-2 text-sm leading-6 text-codey-text">
                    {lane.output}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="relative z-10 mx-auto max-w-6xl px-6 pb-24 md:px-12">
        <div className="overflow-hidden rounded-[28px] border border-codey-border bg-codey-card/40">
          <div className="border-b border-codey-border px-6 py-5">
            <p className="text-xs uppercase tracking-[0.24em] text-codey-text-muted">
              Ops coverage
            </p>
            <h2 className="mt-2 text-2xl font-bold">Why teams move repo maintenance into Codey</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-codey-border text-left text-codey-text-dim">
                  <th className="px-6 py-4 font-medium">Capability</th>
                  <th className="px-6 py-4 text-center font-medium text-codey-green">Codey</th>
                  <th className="px-6 py-4 text-center font-medium">IDE copilots</th>
                  <th className="px-6 py-4 text-center font-medium">CI only</th>
                  <th className="px-6 py-4 text-center font-medium">Dependency bots</th>
                </tr>
              </thead>
              <tbody>
                {coverageRows.map((row, index) => (
                  <tr
                    key={row.feature}
                    className={index % 2 === 0 ? "bg-codey-bg/30" : "bg-transparent"}
                  >
                    <td className="px-6 py-4 text-codey-text">{row.feature}</td>
                    <td className="px-6 py-4"><CoverageIcon value={row.codey} /></td>
                    <td className="px-6 py-4"><CoverageIcon value={row.copilots} /></td>
                    <td className="px-6 py-4"><CoverageIcon value={row.ci} /></td>
                    <td className="px-6 py-4"><CoverageIcon value={row.bots} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="relative z-10 mx-auto max-w-5xl px-6 pb-24 text-center md:px-12">
        <p className="text-xs uppercase tracking-[0.24em] text-codey-text-muted">
          Pricing
        </p>
        <h2 className="mt-3 text-3xl font-bold tracking-tight md:text-4xl">
          Price the operator. Scale the repo fleet.
        </h2>
        <p className="mx-auto mt-4 max-w-2xl text-sm leading-7 text-codey-text-dim">
          Plans are sized around connected repositories, autonomous runs, and the amount
          of maintenance Codey can execute for you each month.
        </p>
        <div className="mt-8">
          <Link href="/pricing" className="btn-primary px-8 py-3 text-base">
            View plans
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>

      <section className="relative z-10 mx-auto max-w-5xl px-6 pb-24 md:px-12">
        <div className="mb-8 text-center">
          <p className="text-xs uppercase tracking-[0.24em] text-codey-text-muted">
            FAQ
          </p>
          <h2 className="mt-3 text-3xl font-bold tracking-tight">Common operator questions</h2>
        </div>
        <FAQ />
      </section>

      <footer className="relative z-10 border-t border-codey-border bg-codey-card/30 px-6 py-10 md:px-12">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-lg font-bold tracking-tight">
              <span className="text-codey-green">C</span>ODEY
            </p>
            <p className="mt-1 text-xs text-codey-text-muted">
              Autonomous repository management by Qira LLC
            </p>
          </div>
          <div className="flex gap-6 text-sm text-codey-text-dim">
            <Link href="/pricing" className="transition-colors hover:text-codey-text">
              Pricing
            </Link>
            <Link href="/auth/login" className="transition-colors hover:text-codey-text">
              Log in
            </Link>
            <Link href="/auth/signup" className="transition-colors hover:text-codey-text">
              Start managing repos
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
