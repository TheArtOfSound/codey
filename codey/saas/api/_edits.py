"""Search/replace edit blocks.

Let the model express a change to an EXISTING file as just the changed region
instead of rewriting (and often truncating) the whole file. This is parsed at
commit time and applied against the freshly cloned repo, so:
  * an edit can never delete the rest of a file (we only touch matched regions)
  * generation costs a fraction of the tokens (no whole-file reprint)

Format the model is asked to produce:

    FILE: path/to/file.ext
    <<<<<<< SEARCH
    <exact consecutive lines copied from the current file>
    =======
    <replacement lines>
    >>>>>>> REPLACE

Multiple blocks are allowed; each is applied independently.
"""

from __future__ import annotations

import re

_PATH_LINE = re.compile(
    r"^[ \t>*\-]*(?:FILE:|EDIT:|```[\w+\-]*)?[ \t]*([\w./\-]+\.[A-Za-z0-9]{1,8})\s*$"
)
_SEARCH = re.compile(r"^[ \t]*<{5,}\s*SEARCH\b")
_DIVIDER = re.compile(r"^[ \t]*={5,}\s*$")
_REPLACE = re.compile(r"^[ \t]*>{5,}\s*REPLACE\b")


def parse_search_replace(output: str) -> list[dict]:
    """Extract search/replace edit blocks from model output.

    Returns a list of {"path": str|None, "search": str, "replace": str}. The
    path is taken from the nearest preceding FILE:/EDIT:/fence/path line.
    """
    if not output:
        return []
    lines = output.split("\n")
    edits: list[dict] = []
    last_path: str | None = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        m = _PATH_LINE.match(line)
        if m and not _SEARCH.match(line):
            last_path = m.group(1)
        if _SEARCH.match(line):
            i += 1
            search: list[str] = []
            while i < n and not _DIVIDER.match(lines[i]):
                search.append(lines[i])
                i += 1
            i += 1  # skip the ======= divider
            replace: list[str] = []
            while i < n and not _REPLACE.match(lines[i]):
                replace.append(lines[i])
                i += 1
            edits.append(
                {
                    "path": last_path,
                    "search": "\n".join(search),
                    "replace": "\n".join(replace),
                }
            )
        i += 1
    return edits


def _flex_apply(content: str, search: str, replace: str) -> str | None:
    """Whitespace-tolerant single application: match ignoring trailing
    whitespace per line, preserving the file's own line endings."""
    c_lines = content.split("\n")
    s_lines = [ln.rstrip() for ln in search.split("\n")]
    while s_lines and s_lines[-1] == "":
        s_lines.pop()
    if not s_lines:
        return None
    for start in range(0, len(c_lines) - len(s_lines) + 1):
        window = [c_lines[start + k].rstrip() for k in range(len(s_lines))]
        if window == s_lines:
            new = c_lines[:start] + replace.split("\n") + c_lines[start + len(s_lines):]
            return "\n".join(new)
    return None


def apply_search_replace(original: str, edits: list[dict]) -> tuple[str, int, list[str]]:
    """Apply edits (each {"search","replace"}) to *original* content.

    Returns (new_content, applied_count, problems). Each SEARCH is applied at
    most once; an exact match is tried first, then a whitespace-tolerant match.
    """
    content = original
    applied = 0
    problems: list[str] = []
    for e in edits:
        search = e.get("search", "")
        replace = e.get("replace", "")
        if not search.strip():
            problems.append("empty SEARCH skipped")
            continue
        if search in content:
            content = content.replace(search, replace, 1)
            applied += 1
            continue
        flexed = _flex_apply(content, search, replace)
        if flexed is not None:
            content = flexed
            applied += 1
        else:
            problems.append("SEARCH text not found in file")
    return content, applied, problems


def edited_paths(output: str) -> list[str]:
    """Distinct file paths referenced by search/replace blocks (for display)."""
    seen: list[str] = []
    for e in parse_search_replace(output):
        p = e.get("path")
        if p and p not in seen:
            seen.append(p)
    return seen


if __name__ == "__main__":
    # Deterministic self-test (no LLM needed).
    original = "\n".join(
        [
            "#!/usr/bin/env node",
            'const OWNER = "TheArtOfSound";',
            'const command = process.argv[2] || "help";',
            "",
            "async function main() {",
            "  return help();",
            "}",
            "",
            "async function install() {",
            "  doInstall();",
            "}",
        ]
    )
    model_output = """Here is the change.

FILE: packages/qev-cli/bin/qev-workspace.js
<<<<<<< SEARCH
const command = process.argv[2] || "help";
=======
const validCommands = ["install", "help"];
const command = process.argv[2] || "help";
if (!validCommands.includes(command)) {
  console.error(`Unknown command: ${command}`);
  process.exit(1);
}
>>>>>>> REPLACE

Operator notes: adds validation, preserves install().
"""
    edits = parse_search_replace(model_output)
    assert len(edits) == 1, edits
    assert edits[0]["path"] == "packages/qev-cli/bin/qev-workspace.js", edits[0]["path"]
    new, applied, problems = apply_search_replace(original, edits)
    assert applied == 1, (applied, problems)
    assert "async function install()" in new, "install() must be preserved"
    assert "validCommands" in new, "edit must be applied"
    assert new.count("async function") == 2, "no functions dropped"
    assert len(new.split("\n")) > len(original.split("\n")), "edit adds lines"
    # whitespace-tolerant match
    edits2 = [{"search": "  return help();  ", "replace": "  return router();"}]
    new2, applied2, _ = apply_search_replace(original, edits2)
    assert applied2 == 1 and "router()" in new2, "flex match failed"
    # non-matching search is reported, not applied destructively
    _, a3, p3 = apply_search_replace(original, [{"search": "nonexistent line", "replace": "x"}])
    assert a3 == 0 and p3, "missing search should be reported"
    print("ALL TESTS PASSED")
    print("applied:", applied, "| problems:", problems)
    print("result lines:", len(new.split("\n")), "(original", len(original.split("\n")), ")")
