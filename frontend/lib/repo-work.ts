export interface RepoWorkLane {
  id: string;
  label: string;
  summary: string;
  signals: string;
  output: string;
  modeLabel: string;
  autopilotReady: boolean;
}

export interface RepoMissionTemplate {
  id: string;
  label: string;
  category: string;
  description: string;
  prompt: string;
}

export const repoWorkLanes: RepoWorkLane[] = [
  {
    id: "fleet-scan",
    label: "Fleet scan",
    summary: "Continuously map structural risk, stale areas, and repo drift across the fleet.",
    signals: "Hotspots, drift, churn, failing checks, aging branches",
    output: "Ranked maintenance queue with focus files and first action",
    modeLabel: "Continuous",
    autopilotReady: true,
  },
  {
    id: "bug-repair",
    label: "Bug repair",
    summary: "Trace regressions to the smallest safe fix and keep the blast radius contained.",
    signals: "Broken flows, runtime errors, failed auth, user-facing regressions",
    output: "Targeted patch with regression notes and rollback risks",
    modeLabel: "Autopilot + queued run",
    autopilotReady: true,
  },
  {
    id: "structural-refactor",
    label: "Structural refactor",
    summary: "Reduce coupling and split stressed files before they become expensive to change.",
    signals: "Low cohesion, high coupling, shared state, cycle pressure",
    output: "Extracted or simplified code with structural rationale",
    modeLabel: "Autopilot + queued run",
    autopilotReady: true,
  },
  {
    id: "dependency-maintenance",
    label: "Dependency maintenance",
    summary: "Handle stale or risky packages without turning every upgrade into a rewrite.",
    signals: "Version drift, policy windows, vulnerable packages, broken lockfiles",
    output: "Safe version updates, migration notes, and package diffs",
    modeLabel: "Autopilot + queued run",
    autopilotReady: true,
  },
  {
    id: "performance-tuning",
    label: "Performance tuning",
    summary: "Address slow paths where repo structure and runtime behavior are compounding.",
    signals: "Hot code paths, heavy fan-out, expensive loops, repeated work",
    output: "Focused optimization with impact notes and guardrails",
    modeLabel: "Autopilot + queued run",
    autopilotReady: true,
  },
  {
    id: "ci-rescue",
    label: "CI rescue",
    summary: "Repair broken builds and test pipelines with the smallest viable change set.",
    signals: "Red pipelines, flaky tests, broken scripts, failing release checks",
    output: "Green-path fix plus rerun checklist",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
  {
    id: "security-hardening",
    label: "Security hardening",
    summary: "Tighten trust boundaries and unsafe patterns before they become incidents.",
    signals: "Secret handling, auth gaps, unsafe eval paths, exposed boundaries",
    output: "Hardened code with risk notes and follow-up checks",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
  {
    id: "test-coverage",
    label: "Test coverage",
    summary: "Add the assertions and fixtures needed to keep fixes from regressing.",
    signals: "Missing regression guards, weak assertions, untested edge cases",
    output: "New or upgraded tests with failure modes called out",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
  {
    id: "pr-review",
    label: "PR review",
    summary: "Read risky diffs like an operator, not a chatbot, and surface what matters first.",
    signals: "Risky files, blast radius, missing tests, unsafe assumptions",
    output: "Prioritized findings and recommended follow-up patch",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
  {
    id: "docs-runbooks",
    label: "Docs and runbooks",
    summary: "Keep setup, operational knowledge, and change notes aligned with the repo.",
    signals: "Stale README steps, missing rollout notes, tribal knowledge gaps",
    output: "Updated docs, changelog text, and operator instructions",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
  {
    id: "release-deploy",
    label: "Release and deploy",
    summary: "Close the gap between a code fix and something that can actually ship.",
    signals: "Env drift, bad routing, broken deploy config, release blockers",
    output: "Deployment patch, rollout notes, and verification path",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
  {
    id: "tooling-automation",
    label: "Tooling and automation",
    summary: "Turn recurring repo chores into scripts, commands, and repeatable workflows.",
    signals: "Manual setup, repeated shell work, missing developer tooling",
    output: "CLI, scripts, workflow changes, and maintenance automation",
    modeLabel: "Queued run",
    autopilotReady: false,
  },
];

export const repoMissionTemplates: RepoMissionTemplate[] = [
  {
    id: "repo-sweep",
    label: "Repo sweep",
    category: "Scan",
    description: "Rank the top maintenance work and pick one safe first intervention.",
    prompt:
      "Scan this repository like a full-time repo operator. Rank the highest-value maintenance work by blast radius and urgency, choose one safe first intervention, update the most relevant existing files, and return operator notes with verification steps and follow-up risks.",
  },
  {
    id: "incident-fix",
    label: "Incident fix",
    category: "Repair",
    description: "Patch a live regression while keeping behavior stable outside the target area.",
    prompt:
      "Fix the current regression in this repository with the smallest safe patch. Preserve behavior outside the affected flow, explain the failure mode, update the relevant existing files first, and include regression checks that should be rerun before shipping.",
  },
  {
    id: "ci-rescue",
    label: "CI rescue",
    category: "Verify",
    description: "Restore a broken build or flaky pipeline without broad churn.",
    prompt:
      "Find why CI is failing in this repository and fix the narrowest set of files needed to restore a green pipeline. Keep blast radius explicit, call out any flaky assumptions, and include the commands or checks that should be rerun.",
  },
  {
    id: "dependency-run",
    label: "Dependency run",
    category: "Maintain",
    description: "Update stale or risky packages with migration notes attached.",
    prompt:
      "Prepare a dependency maintenance run for this repository. Update stale or vulnerable packages with minimal blast radius, adjust lockfiles or config as needed, flag migration risk, and summarize the package changes in changelog-ready notes.",
  },
  {
    id: "security-pass",
    label: "Security pass",
    category: "Secure",
    description: "Harden auth, secrets, and unsafe boundaries in the current repo state.",
    prompt:
      "Perform a security hardening pass on this repository focused on the most exposed trust boundary. Fix the highest-risk issue in the existing code, preserve intended behavior, and include concise notes about risk reduction, remaining gaps, and recommended follow-up checks.",
  },
  {
    id: "test-guardrails",
    label: "Test guardrails",
    category: "Verify",
    description: "Add regression coverage where the repo is currently most exposed.",
    prompt:
      "Add the highest-value regression guardrails to this repository. Prefer tests around recent or fragile behavior, keep fixtures lean, update the relevant existing test files first, and include notes about the failure modes now covered.",
  },
  {
    id: "pr-review",
    label: "PR review",
    category: "Review",
    description: "Read the work like a reviewer and produce findings before polishing code.",
    prompt:
      "Review the current repository state like a senior code reviewer. Identify the most important correctness, regression, security, and testing risks first, then apply the smallest fix for the top issue and summarize the remaining findings in priority order.",
  },
  {
    id: "docs-runbook",
    label: "Docs runbook",
    category: "Document",
    description: "Refresh README, rollout notes, and operator documentation around a change.",
    prompt:
      "Update this repository's docs and runbooks to match the current implementation. Fix stale setup or deployment instructions, add concise operator-facing notes where they are missing, and keep the final output practical enough to use during maintenance or release work.",
  },
  {
    id: "release-blocker",
    label: "Release blocker",
    category: "Ship",
    description: "Resolve the issue preventing a release or deployment from going out cleanly.",
    prompt:
      "Unblock release or deployment for this repository. Fix the most likely config, routing, environment, or runtime issue stopping the ship path, keep the change narrowly scoped, and include rollout notes plus the exact checks that should pass before go-live.",
  },
  {
    id: "tooling-upgrade",
    label: "Tooling upgrade",
    category: "Automate",
    description: "Turn manual repo work into scripts, CLI commands, or workflow cleanup.",
    prompt:
      "Improve repository tooling so recurring maintenance takes less manual work. Add or refine the smallest useful automation, script, CLI, or workflow first, prefer existing project patterns, and explain how the new tooling should be used in normal repo operations.",
  },
];
