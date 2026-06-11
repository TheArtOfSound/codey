#!/usr/bin/env bash
set -euo pipefail

VAULT="${1:-$HOME/Desktop/llm-agent-architecture-vault}"
mkdir -p "$VAULT"
cd "$VAULT"

printf '\n[Codey Research] Vault: %s\n' "$VAULT"
printf '[Codey Research] Pulling public LLM/agent architecture sources and building an index.\n\n'

safe_dir_name() {
  python3 - "$1" <<'PY'
import re, sys
url = sys.argv[1].strip().rstrip('/').replace('.git','')
parts = url.split('/')
name = (parts[-2] + '__' + parts[-1]) if len(parts) >= 2 else url
print(re.sub(r'[^A-Za-z0-9_.-]+', '_', name))
PY
}

clone_url() {
  local url="$1"
  [[ -z "$url" ]] && return 0
  [[ "$url" != https://github.com/* ]] && return 0
  local dir
  dir="$(safe_dir_name "$url")"
  if [[ -d "$dir/.git" ]]; then
    printf '[skip] %s\n' "$dir"
    return 0
  fi
  printf '[clone] %s -> %s\n' "$url" "$dir"
  git clone --depth 1 "$url" "$dir" || printf '[warn] clone failed: %s\n' "$url"
}

cat > seed-open-agent-repos.txt <<'EOF'
https://github.com/langchain-ai/open-swe
https://github.com/plandex-ai/plandex
https://github.com/All-Hands-AI/OpenHands
https://github.com/SWE-agent/SWE-agent
https://github.com/Significant-Gravitas/AutoGPT
https://github.com/geekan/MetaGPT
https://github.com/Codium-ai/pr-agent
https://github.com/continuedev/continue
https://github.com/Aider-AI/aider
https://github.com/cline/cline
https://github.com/RooVetGit/Roo-Code
https://github.com/openclaw/openclaw
https://github.com/opencode-ai/opencode
https://github.com/sst/opencode
https://github.com/langchain-ai/langgraph
https://github.com/crewAIInc/crewAI
https://github.com/microsoft/autogen
https://github.com/microsoft/semantic-kernel
https://github.com/google/adk-python
https://github.com/OpenBMB/ChatDev
https://github.com/mixpeek/amux
https://github.com/generalaction/emdash
EOF

printf '[phase] cloning known open agent repos...\n'
sort -u seed-open-agent-repos.txt > seed-open-agent-repos.unique.txt
while IFS= read -r url; do
  clone_url "$url"
done < seed-open-agent-repos.unique.txt

cat > github-search-queries.txt <<'EOF'
AI coding agent open source
LLM coding agent open source
agentic coding assistant open source
software engineering agent LLM
LLM agent framework tool use
MCP agent framework
AI agent tools.json
AI agent permission sandbox
AI agent context compaction
AI agent memory system
AI agent subagent worktree
AI product builder agent
v0 Vercel prompt archive
Lovable agent prompt archive
Cursor agent prompt archive
Windsurf agent prompt archive
Replit agent prompt tools
Devin agent prompt archive
Manus agent tools prompt
Claude Code architecture analysis
Claude Code source analysis
Gemini agent prompt archive
Grok agent prompt archive
Perplexity prompt archive
Copilot prompt archive
ChatGPT prompt archive
OpenAI prompt archive
Anthropic prompt archive
EOF

printf '\n[phase] discovering public repos from GitHub search...\n'
: > discovered-urls.txt

if command -v gh >/dev/null 2>&1; then
  printf '[info] using gh CLI for search\n'
  while IFS= read -r q; do
    [[ -z "$q" ]] && continue
    printf '[search] %s\n' "$q"
    gh search repos "$q" --limit 40 --json url --jq '.[].url' >> discovered-urls.txt 2>/dev/null || true
    sleep 1
  done < github-search-queries.txt
else
  printf '[info] gh CLI not found; using GitHub REST API fallback with python3. Rate limits may apply.\n'
  python3 <<'PY'
import json, os, time, urllib.parse, urllib.request
headers = {"Accept": "application/vnd.github+json", "User-Agent": "codey-research-vault"}
tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
if tok:
    headers["Authorization"] = f"Bearer {tok}"
queries = [q.strip() for q in open("github-search-queries.txt", encoding="utf-8") if q.strip()]
out = open("discovered-urls.txt", "a", encoding="utf-8")
for q in queries:
    print(f"[search] {q}")
    for page in range(1, 3):
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": q, "per_page": 30, "page": page})
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            for item in data.get("items", []):
                html = item.get("html_url")
                if html:
                    out.write(html + "\n")
            out.flush()
        except Exception as e:
            print(f"[warn] query failed: {q} page={page}: {e}")
            break
        time.sleep(2)
out.close()
PY
fi

sort -u discovered-urls.txt > discovered-urls.unique.txt

printf '\n[phase] cloning discovered architecture/framework repos...\n'
while IFS= read -r url; do
  case "$url" in
    *system-prompt*|*System-Prompt*|*prompts*|*Prompts*|*prompt-archive*|*source-map*|*sourcemap*)
      printf '[catalog-only] %s\n' "$url" >> catalog-only-urls.txt
      ;;
    *)
      clone_url "$url"
      ;;
  esac
done < discovered-urls.unique.txt

printf '\n[phase] building indexes...\n'
find . -maxdepth 2 -type d -name .git | sed 's#/.git##' | sort > repo-list.txt
find . -type f \( -name '*.md' -o -name '*.txt' -o -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.py' -o -name '*.go' -o -name '*.rs' \) > file-index.txt

PATTERN="permission|permissions|sandbox|tool|tools|MCP|plugin|hook|skill|memory|compaction|context|subagent|worktree|session|slash|command|artifact|walkthrough|plan|verify|validation|agent loop|browser|shell|terminal|edit|diff|checkpoint|policy"
if command -v rg >/dev/null 2>&1; then
  rg -n --glob '!**/.git/**' "$PATTERN" . > high-value-hits.txt || true
else
  grep -RInE "$PATTERN" . > high-value-hits.txt 2>/dev/null || true
fi

python3 <<'PY'
from pathlib import Path
repos = sorted(Path('repo-list.txt').read_text(errors='ignore').splitlines()) if Path('repo-list.txt').exists() else []
files = sorted(Path('file-index.txt').read_text(errors='ignore').splitlines()) if Path('file-index.txt').exists() else []
hits = Path('high-value-hits.txt').read_text(errors='ignore').splitlines() if Path('high-value-hits.txt').exists() else []
catalog = sorted(set(Path('catalog-only-urls.txt').read_text(errors='ignore').splitlines())) if Path('catalog-only-urls.txt').exists() else []
summary = ['# LLM Agent Architecture Vault Summary', '', f'- Repositories cloned/indexed: {len(repos)}', f'- Candidate files indexed: {len(files)}', f'- High-value keyword hits: {len(hits)}', f'- Prompt/archive URLs cataloged but not cloned: {len(catalog)}', '', '## Cloned repos', '']
summary.extend(f'- `{r}`' for r in repos[:400])
summary.extend(['', '## Catalog-only URLs', ''])
summary.extend(f'- {u}' for u in catalog[:400])
summary.extend(['', '## Extraction targets', '', '- Agent loop and execution model', '- Tool schema and routing', '- Permission/sandbox model', '- Memory/context compaction', '- Subagent/worktree/session model', '- UI/artifact rules', '- Validation/testing/reporting loop'])
Path('VAULT_SUMMARY.md').write_text('\n'.join(summary), encoding='utf-8')
PY

printf '\n[done] Vault ready.\n'
printf 'Open: %s\n' "$VAULT"
printf 'Summary: %s/VAULT_SUMMARY.md\n' "$VAULT"
open "$VAULT" 2>/dev/null || true
