import { Megaphone, Tag, Calendar } from "lucide-react";

interface ChangelogEntry {
  version: string;
  date: string;
  title: string;
  description: string;
  features: string[];
  type: "major" | "minor" | "patch";
}

const CHANGELOG: ChangelogEntry[] = [
  {
    version: "1.0.0",
    date: "March 27, 2026",
    title: "Codey is live",
    description:
      "The first public release of Codey. Everything you need to go from a plain-English prompt to production-ready code.",
    type: "major",
    features: [
      "AI code generation from natural language prompts with streaming output",
      "NFET structural health analysis for every generated project",
      "Code Vault with full version history, file tree browsing, and restore",
      "Memory system that learns your coding preferences across 7 dimensions",
      "Credit-based usage with Free, Pro, and Team plans",
      "Stripe-powered billing with credit top-ups and subscription management",
      "GitHub integration for repo linking and autonomous monitoring",
      "Autonomous mode: Codey watches your repos and suggests fixes",
      "Export center: download as ZIP, push to GitHub, or send via webhook",
      "Session history with NFET health tracking over time",
      "Referral program: earn credits by inviting other developers",
      "Full settings page with profile, notifications, API keys, and billing",
      "Dark-themed responsive UI built for developers",
    ],
  },
];

function versionColor(type: ChangelogEntry["type"]): string {
  switch (type) {
    case "major":
      return "bg-codey-green/20 text-codey-green border-codey-green/30";
    case "minor":
      return "bg-codey-yellow/20 text-codey-yellow border-codey-yellow/30";
    case "patch":
      return "bg-codey-text-dim/20 text-codey-text-dim border-codey-text-dim/30";
  }
}

export default function ChangelogPage() {
  return (
    <div className="min-h-screen bg-codey-bg">
      <div className="mx-auto max-w-3xl px-4 py-16">
        <div className="flex items-center gap-3">
          <Megaphone className="h-6 w-6 text-codey-green" />
          <h1 className="text-2xl font-bold text-codey-text">Changelog</h1>
        </div>
        <p className="mt-2 text-sm text-codey-text-dim">
          New features, improvements, and fixes. Follow along as Codey evolves.
        </p>

        <div className="mt-10 space-y-8">
          {CHANGELOG.map((entry) => (
            <article
              key={entry.version}
              className="rounded-xl border border-codey-border bg-codey-card"
            >
              <div className="border-b border-codey-border/50 px-6 py-5">
                <div className="flex flex-wrap items-center gap-3">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-bold ${versionColor(entry.type)}`}
                  >
                    <Tag className="h-3.5 w-3.5" />
                    v{entry.version}
                  </span>
                  <span className="flex items-center gap-1.5 text-xs text-codey-text-muted">
                    <Calendar className="h-3 w-3" />
                    {entry.date}
                  </span>
                </div>
                <h2 className="mt-3 text-xl font-bold text-codey-text">{entry.title}</h2>
                <p className="mt-2 text-sm text-codey-text-dim">{entry.description}</p>
              </div>

              <div className="px-6 py-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-codey-text-muted">
                  What&apos;s Included
                </h3>
                <ul className="mt-3 space-y-2">
                  {entry.features.map((feature, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-codey-text-dim">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-codey-green" />
                      {feature}
                    </li>
                  ))}
                </ul>
              </div>
            </article>
          ))}
        </div>

        <div className="mt-12 text-center">
          <p className="text-sm text-codey-text-muted">
            More updates coming soon. Follow{" "}
            <a href="#" className="text-codey-green hover:underline">
              @codeyai
            </a>{" "}
            for announcements.
          </p>
        </div>
      </div>
    </div>
  );
}
