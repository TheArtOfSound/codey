"""Best-effort extraction of {path: content} from an LLM markdown answer, so a
synchronous prompt session can persist committable files for the commit flow."""

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


def extract_generated_files(output: str, prompt: str, language: str) -> dict:
    """Turn fenced code blocks into {path: content}.

    Filenames come from a fence label (```lang path or ```path), else the file
    path named in the prompt (for a single block), else a generated default.
    """
    files: dict = {}
    _pmatches = _PATH.findall(prompt or "")
    _slashed = [m for m in _pmatches if "/" in m]
    # Prefer the longest repo-relative path (one with a slash) over a bare
    # basename, so "fix foo.js in `pkg/bin/foo.js`" targets pkg/bin/foo.js
    # rather than creating a stray foo.js at the repo root.
    prompt_path = max(_slashed, key=len) if _slashed else (_pmatches[0] if _pmatches else None)
    lang_default = (language or "python").lower()
    blocks = _FENCE.findall(output or "")
    for i, (info, code) in enumerate(blocks):
        if not code.strip():
            continue
        content = code.rstrip("\n") + "\n"
        info = (info or "").strip()
        path = None
        for tok in info.split():
            tok = tok.strip(":")
            if "/" in tok or re.search(r"\.[A-Za-z0-9]{1,6}$", tok):
                path = tok
                break
        if not path:
            if len(blocks) == 1 and prompt_path:
                path = prompt_path
            else:
                lang = info.split()[0].lower() if info.split() else lang_default
                ext = _EXT.get(lang, _EXT.get(lang_default, "txt"))
                path = prompt_path if (prompt_path and len(blocks) == 1) else f"generated_{i + 1}.{ext}"
        files[path] = content
    if not files and (output or "").strip():
        ext = _EXT.get(lang_default, "txt")
        files[prompt_path or f"generated.{ext}"] = output.strip() + "\n"
    return files
