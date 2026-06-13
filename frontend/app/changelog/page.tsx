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
      "The first public release of Codey as an autonomous repo operator for scan, repair, CI, security, docs, and release management.",
    type: "major",
    features: [
      "Repo scan control room with maintenance lanes across bugs, CI, security, docs, and releases",
      "Intervention console with mission templates that map 1:1 to Codey's repo-work coverage",
      "Code Vault with version history, file tree browsing, and restore",
      "Memory system that learns repo and operator preferences across sessions",
      "Credit-based usage with Free, Pro, and Team plans",
      "Stripe-powered billing with credit top-ups and subscription management",
      "GitHub integration for repo linking, scanning, and autonomous monitoring",
      "Autopilot controls for recurring repo runs and approval boundaries",
      "Run history with health tracking over time",
      "Export center for patch and artifact delivery",
      "Settings page with profile, notifications, API keys, and billing",
      "Responsive operator interface built for repository fleets",
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
            <a
              href="https://github.com/TheArtOfSound/codey"
              target="_blank"
              rel="noreferrer"
              className="text-codey-green hover:underline"
            >
              the GitHub repo
            </a>{" "}
            for announcements.
          </p>
        </div>
      </div>
    </div>
  );
}
