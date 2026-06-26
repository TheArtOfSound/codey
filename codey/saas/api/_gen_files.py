"""Best-effort extraction of {path: content} from an LLM markdown answer, so a
synchronous prompt session can persist committable files for the commit flow.

Path resolution order for each fenced block:
  1. an explicit ``FILE: <path>`` / ``PATH: <path>`` marker on the line right
     before the fence (the contract the generation system prompt enforces, and
     the format the repo-grounding context uses);
  2. a path token in the fence info string (```ts src/foo.ts);
  3. for a single-block answer, a repo-relative path named in the prompt;
  4. a namespaced fallback under ``codey-generated/`` — never a bare
     ``generated_N.ext`` dumped at the repository root.
"""

from __future__ import annotations

import re

_EXT = {
    "python": "py", "py": "py", "javascript": "js", "js": "js",
    "typescript": "ts", "ts": "ts", "tsx": "tsx", "jsx": "jsx",
    "html": "html", "css": "css", "json": "json", "bash": "sh", "sh": "sh",
    "go": "go", "rust": "rs", "rs": "rs", "java": "java", "c": "c", "cpp": "cpp",
    "yaml": "yaml", "yml": "yml", "toml": "toml", "sql": "sql",
}

_FENCE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)
_PATH = re.compile(r"[\w./-]+/[\w./-]+\.[A-Za-z0-9]{1,6}|[\w-]+\.[A-Za-z0-9]{1,6}")
_FILE_MARKER = re.compile(
    r"(?:FILE|File|file|PATH|Path|path)\s*[:=]\s*[`'\"]?([\w./@+-]+)[`'\"]?\s*$"
)


def _clean_path(p: object) -> str | None:
    if not isinstance(p, str):
        return None
    p = p.strip().strip("`'\"").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if not p or "://" in p or ".." in p.split("/"):
        return None
    # must look like a file (have an extension somewhere)
    if not re.search(r"\.[A-Za-z0-9]{1,8}$", p):
        return None
    return p


def extract_generated_files(output: str, prompt: str, language: str) -> dict:
    """Turn fenced code blocks into ``{path: content}``."""
    files: dict = {}
    _pmatches = _PATH.findall(prompt or "")
    _slashed = [m for m in _pmatches if "/" in m]
    # Prefer the longest repo-relative path (one with a slash) over a bare
    # basename, so "fix foo.js in `pkg/bin/foo.js`" targets pkg/bin/foo.js.
    prompt_path = max(_slashed, key=len) if _slashed else (_pmatches[0] if _pmatches else None)
    lang_default = (language or "python").lower()
    text = output or ""
    matches = list(_FENCE.finditer(text))
    for i, m in enumerate(matches):
        info = (m.group(1) or "").strip()
        code = m.group(2)
        if not code.strip():
            continue
        content = code.rstrip("\n") + "\n"
        path = None

        # 1. FILE:/PATH: marker on the non-empty line just before the fence.
        window = text[max(0, m.start() - 240):m.start()]
        wlines = [ln for ln in window.splitlines() if ln.strip()]
        if wlines:
            fm = _FILE_MARKER.search(wlines[-1])
            if fm:
                path = _clean_path(fm.group(1))

        # 2. Path token in the fence info string.
        if not path:
            for tok in info.split():
                tok = tok.strip(":")
                if "/" in tok or re.search(r"\.[A-Za-z0-9]{1,6}$", tok):
                    path = _clean_path(tok)
                    if path:
                        break

        # 3. Single block: a repo-relative path named in the prompt.
        if not path and len(matches) == 1 and prompt_path:
            path = _clean_path(prompt_path)

        # 4. Namespaced fallback — never a bare file at the repo root.
        if not path:
            lang = info.split()[0].lower() if info.split() else lang_default
            ext = _EXT.get(lang, _EXT.get(lang_default, "txt"))
            path = f"codey-generated/snippet_{i + 1}.{ext}"

        files[path] = content

    if not files and text.strip():
        ext = _EXT.get(lang_default, "txt")
        files[_clean_path(prompt_path) or f"codey-generated/output.{ext}"] = text.strip() + "\n"
    return files
