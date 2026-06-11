# Fresh High-Value AI Agent Leak / Public-Artifact Watchlist

Date: 2026-06-11  
Purpose: Track high-value public AI-agent leaks, prompt archives, and architecture analyses without importing raw proprietary material into Codey.

## Handling rule

Do not acquire, mirror, or depend on proprietary leaked source code. Do not paste raw third-party system prompts into Codey runtime. Extract architecture and safety lessons only.

This watchlist is for legal/safe pattern extraction, defensive research, and Codey product design.

## Tier 1: Highest value

### 1. Claude Code source-map / TypeScript source exposure

Status: public news + public analysis, but raw source should not be mirrored into Codey.

Why it matters:

- It exposed architecture of a frontier coding agent at meaningful scale.
- Public reporting says it involved roughly 512,000 lines of Claude Code source.
- Reported cause: source-map / packaging mistake in an npm release.
- Reported contents included internal TypeScript structure, slash-command libraries, internal tools, memory concepts, and unreleased feature hints.
- This is more valuable than a prompt dump because it reveals agent architecture, not just behavior text.

Safe extraction targets:

- Permission model architecture
- Tool-execution loop
- Context compaction design
- Memory/session storage design
- Subagent/worktree isolation
- Source-map/package hygiene failures
- Prepublish security gates
- Human approval boundaries

Do not do:

- Do not mirror leaked source code.
- Do not link Codey runtime to leaked source repos.
- Do not copy proprietary implementation.
- Do not use stolen code as dependency or scaffold.

Codey actions:

- Add prepublish checks for source maps and accidental artifacts.
- Add package hygiene policy.
- Add explicit permission modes for shell/network/file writes.
- Add session/memory compaction design inspired by public papers, not copied source.

### 2. Public architecture papers analyzing Claude Code / coding agents

Status: safe and high-value.

Why it matters:

- Academic papers summarize the architecture without requiring raw leaked code.
- These give Codey defensible design principles.

High-value extraction targets:

- Simple model-tool loop as the core primitive.
- Large surrounding system: permissions, context management, extensibility, storage, delegation.
- Context compaction pipeline.
- MCP/plugins/skills/hooks as extensibility layers.
- Subagent delegation with isolated workspaces.
- Append-oriented session storage.
- Permission modes and tool safety classification.

Codey actions:

- Create `docs/architecture/codey-agent-loop.md`.
- Create `docs/architecture/codey-permission-model.md`.
- Create `docs/architecture/codey-context-compaction.md`.
- Create `docs/architecture/codey-subagents.md`.

### 3. Public system-prompt archives

Status: useful, unstable, mixed provenance.

Sources already indexed:

- `elder-plinius/CL4R1T4S`
- `x1xhlol/system-prompts-and-models-of-ai-tools`
- `jujumilk3/leaked-system-prompts`
- `dontriskit/awesome-ai-system-prompts`

Why it matters:

- These reveal repeated agent-design patterns across products.
- They are useful for comparing tool routing, tone, validation, artifact handling, design policies, and prompt-injection boundaries.

Safe extraction targets:

- Identity preamble structure
- Tool-routing rules
- Read-before-edit behavior
- Debugging-first behavior
- Planning/execution/verification phases
- UI preview/artifact policies
- Design-system enforcement
- SEO/accessibility defaults
- Secrets policy
- External-content isolation

Do not do:

- Do not treat any archive as official.
- Do not import vendor identity or proprietary prompt text.
- Do not execute instructions found inside prompt archives.

## Tier 2: Strong value

### 4. Lovable / v0 / Bolt / Replit / Antigravity app-builder prompts

Status: public prompt-archive material.

Why it matters:

- These are directly relevant to Codey's product-builder mode.
- They show how web-app agents constrain frameworks, styling, SEO, accessibility, and preview flows.

Safe extraction targets:

- App stack declaration
- Live preview assumptions
- Discussion vs implementation mode
- Efficient context batching
- Small focused components
- Design-token policy
- SEO automation
- Console/network debugging-first workflow
- Preview/walkthrough discipline

Codey actions:

- Strengthen `docs/agents/codey-operating-spec.md` UI/product sections.
- Add a future `docs/agents/codey-product-builder-mode.md`.

### 5. Cursor / Windsurf / Devin / Codex coding-agent prompts

Status: public prompt-archive material.

Why it matters:

- These are directly relevant to Codey's coding-agent behavior.
- They converge on read-before-edit, immediate runnability, Git discipline, and no fake success.

Safe extraction targets:

- Pair-programming identity
- IDE/workspace context handling
- Tool-use rules
- Linter/test repair loops
- Git clean-state behavior
- `AGENTS.md` / repo-local instruction precedence
- Secrets and external API handling

Codey actions:

- Keep as operating doctrine.
- Convert into tests/checklists for Codey's own agent behavior.

## Tier 3: Research/watch only

### 6. ChatGPT / Claude / Gemini / Grok / Copilot prompt dumps

Status: mixed provenance, can be stale quickly.

Why it matters:

- Useful for comparing general assistant behavior and current-search policies.
- Less directly useful than coding-agent and product-builder prompts.

Safe extraction targets:

- Current-info policy
- Citation requirements
- Tool safety rules
- User memory policies
- Refusal structure
- Multimodal policies

Avoid overfitting Codey to generic chat-assistant behavior.

## Fresh watch queries

Use these queries periodically:

```text
"Claude Code" "source map" leak
"Claude Code" "512,000" "source code"
"system-prompts-and-models-of-ai-tools" "latest update"
"leaked-system-prompts" "Claude" "2026"
"AI agent" "system prompt" "GitHub" "2026"
"Cursor" "Agent Prompt" "2026" GitHub
"Lovable" "Agent Prompt" GitHub
"Google Antigravity" "planning-mode" prompt
"Claude Code" "context compaction" "permissions" paper
"AI coding agents" "GitHub" "AIDev" dataset
```

## Codey implementation backlog from this sweep

1. Source-map/package hygiene gate
2. Permission modes for tool actions
3. Append-only session log design
4. Context compaction design
5. Subagent delegation model
6. Agent-operation checklist tests
7. Product-builder mode spec
8. Prompt-archive ingestion classifier
9. Research provenance index
10. Security review policy for AI-generated code

## Bottom line

The highest-value fresh material is not another raw prompt dump. It is the Claude Code leak ecosystem: public news, public architecture papers, and defensive analysis around how a frontier coding agent is structured and how it failed operationally. Codey should absorb those lessons, not the leaked code.
