# Aggressive Public AI Agent Source Index

Date: 2026-06-11  
Purpose: high-signal public GitHub acquisition/index plan for AI-agent prompts, source-map mirrors, and architecture analysis.

## Rule for Codey

Keep raw third-party dumps outside Codey's source tree. Pull them locally into a separate research folder, then extract architecture into Codey-original specs.

Recommended local folder:

```bash
mkdir -p ~/Desktop/ai-agent-source-vault
cd ~/Desktop/ai-agent-source-vault
```

## Tier 1: Claude Code source-map / source mirrors

These are the most aggressive/high-value targets because they are architecture-rich, not just prompt dumps.

```bash
cd ~/Desktop/ai-agent-source-vault

git clone https://github.com/hangsman/claude-code-source.git || true
git clone https://github.com/Onewon/claude-code.git || true
git clone https://github.com/AprilNEA/claude-code-source.git || true
git clone https://github.com/alejandrobalderas/claude-code-from-source.git || true
git clone https://github.com/leeyeel/claude-code-sourcemap.git || true
git clone https://github.com/xorespesp/claude-code.git || true
git clone https://github.com/OrcaWhisper/Claude-Code.git || true
git clone https://github.com/Safphere/claude-code.git || true
git clone https://github.com/davccavalcante/claude-code-leaked.git || true
git clone https://github.com/hwlv/claude-code-source.git || true
git clone https://github.com/SatoMini/claude-code-source-map.git || true
```

Priority extraction targets:

- CLI command architecture
- Slash-command registry
- Tool execution loop
- Permission modes
- Context compaction
- Session persistence
- Memory/dreaming/autodream concepts
- MCP/plugin/hook/skill mechanisms
- Subagent/worktree isolation
- Source-map/package hygiene failure mode
- Prepublish checks Codey should add

## Tier 2: Claude Code analysis repos

These are safer and often more directly useful than raw mirrors.

```bash
cd ~/Desktop/ai-agent-source-vault

git clone https://github.com/catyans/claude-code-source-analysis.git || true
git clone https://github.com/bcefghj/ClaudeCode-Source-Analysis.git || true
git clone https://github.com/waiterxiaoyy/Deep-Dive-Claude-Code.git || true
git clone https://github.com/alchaincyf/claude-code-source-analysis-orange-book.git || true
git clone https://github.com/thtskaran/claude-code-analysis.git || true
git clone https://github.com/phodal/claude-code-codex-slide.git || true
git clone https://github.com/Troyanovsky/claude-code-analysis.git || true
git clone https://github.com/aaronlab/claude-code-source-analysis.git || true
git clone https://github.com/JimmyWangJimmy/claude-code-source-analysis.git || true
```

Priority extraction targets:

- System diagrams
- Module maps
- Agent-loop summaries
- Permission-model summaries
- Compaction summaries
- Security takeaways
- Product ideas that can be rebuilt cleanly

## Tier 3: Prompt/system-prompt archives

These are high-signal for behavior, less high-signal for architecture.

```bash
cd ~/Desktop/ai-agent-source-vault

git clone https://github.com/elder-plinius/CL4R1T4S.git || true
git clone https://github.com/x1xhlol/system-prompts-and-models-of-ai-tools.git || true
git clone https://github.com/jujumilk3/leaked-system-prompts.git || true
git clone https://github.com/dontriskit/awesome-ai-system-prompts.git || true
git clone https://github.com/axtrur/awesome-ai-system-prompts.git || true
git clone https://github.com/langgptai/awesome-system-prompts.git || true
```

Priority extraction targets:

- Coding-agent operating rules
- Product-builder rules
- Design-system rules
- Search/freshness rules
- File/artifact rules
- Prompt-injection boundaries
- Tool-routing rules
- Validation/reporting rules

## Tier 4: Open-source agent systems for clean implementation inspiration

These are safer for implementation reference because they are intentionally open source.

```bash
cd ~/Desktop/ai-agent-source-vault

git clone https://github.com/openclaw/openclaw.git || true
```

Priority extraction targets:

- Open-source agent loop
- Gateway architecture
- Permission boundary design
- Plugin/capability registration
- Local-first agent patterns

## Local indexing commands

After cloning:

```bash
cd ~/Desktop/ai-agent-source-vault
find . -maxdepth 2 -type d -name .git | sed 's#/.git##' | sort > repo-list.txt
find . -type f \( -name '*.md' -o -name '*.txt' -o -name '*.ts' -o -name '*.tsx' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' \) > file-index.txt
wc -l file-index.txt
```

Search high-value terms:

```bash
cd ~/Desktop/ai-agent-source-vault
rg -n "permission|permissions|allow|deny|sandbox|tool|MCP|plugin|hook|skill|memory|compaction|context|subagent|worktree|session|slash|command|sourceMap|sourcemap|prepublish|artifact|walkthrough|plan|verify|validation" . > high-value-hits.txt
```

Generate a compact extraction pack:

```bash
cd ~/Desktop/ai-agent-source-vault
mkdir -p _extracted
cp repo-list.txt file-index.txt high-value-hits.txt _extracted/
open _extracted
```

## Codey ingestion plan

Do not copy raw files into Codey. Instead generate these Codey-native docs:

- `docs/architecture/codey-agent-loop.md`
- `docs/architecture/codey-permission-model.md`
- `docs/architecture/codey-context-compaction.md`
- `docs/architecture/codey-memory-system.md`
- `docs/architecture/codey-subagents.md`
- `docs/security/source-map-package-hygiene.md`
- `docs/agents/codey-product-builder-mode.md`
- `docs/agents/codey-prompt-archive-ingestion.md`

## Fast local command bundle

```bash
mkdir -p ~/Desktop/ai-agent-source-vault
cd ~/Desktop/ai-agent-source-vault

git clone https://github.com/hangsman/claude-code-source.git || true
git clone https://github.com/Onewon/claude-code.git || true
git clone https://github.com/AprilNEA/claude-code-source.git || true
git clone https://github.com/alejandrobalderas/claude-code-from-source.git || true
git clone https://github.com/leeyeel/claude-code-sourcemap.git || true
git clone https://github.com/xorespesp/claude-code.git || true
git clone https://github.com/OrcaWhisper/Claude-Code.git || true
git clone https://github.com/Safphere/claude-code.git || true
git clone https://github.com/davccavalcante/claude-code-leaked.git || true
git clone https://github.com/hwlv/claude-code-source.git || true
git clone https://github.com/SatoMini/claude-code-source-map.git || true

git clone https://github.com/catyans/claude-code-source-analysis.git || true
git clone https://github.com/bcefghj/ClaudeCode-Source-Analysis.git || true
git clone https://github.com/waiterxiaoyy/Deep-Dive-Claude-Code.git || true
git clone https://github.com/alchaincyf/claude-code-source-analysis-orange-book.git || true
git clone https://github.com/thtskaran/claude-code-analysis.git || true
git clone https://github.com/phodal/claude-code-codex-slide.git || true
git clone https://github.com/Troyanovsky/claude-code-analysis.git || true
git clone https://github.com/aaronlab/claude-code-source-analysis.git || true
git clone https://github.com/JimmyWangJimmy/claude-code-source-analysis.git || true

git clone https://github.com/elder-plinius/CL4R1T4S.git || true
git clone https://github.com/x1xhlol/system-prompts-and-models-of-ai-tools.git || true
git clone https://github.com/jujumilk3/leaked-system-prompts.git || true
git clone https://github.com/dontriskit/awesome-ai-system-prompts.git || true
git clone https://github.com/axtrur/awesome-ai-system-prompts.git || true
git clone https://github.com/langgptai/awesome-system-prompts.git || true

git clone https://github.com/openclaw/openclaw.git || true

find . -maxdepth 2 -type d -name .git | sed 's#/.git##' | sort > repo-list.txt
find . -type f \( -name '*.md' -o -name '*.txt' -o -name '*.ts' -o -name '*.tsx' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' \) > file-index.txt
rg -n "permission|permissions|allow|deny|sandbox|tool|MCP|plugin|hook|skill|memory|compaction|context|subagent|worktree|session|slash|command|sourceMap|sourcemap|prepublish|artifact|walkthrough|plan|verify|validation" . > high-value-hits.txt
open ~/Desktop/ai-agent-source-vault
```
