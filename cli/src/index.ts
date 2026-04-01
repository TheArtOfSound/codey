#!/usr/bin/env node
/**
 * Codey CLI — AI coding agent for your terminal.
 *
 * Usage:
 *   codey "add error handling to the auth module"
 *   codey analyze
 *   codey fix
 *   codey chat
 *
 * Reads your project files, sends them as context to Codey's API,
 * streams the response, applies changes, runs tests, and self-corrects.
 */

import { createInterface } from "readline";
import { existsSync, readFileSync, writeFileSync, readdirSync, statSync } from "fs";
import { join, relative, extname } from "path";
import { execSync } from "child_process";

const API_BASE = process.env.CODEY_API || "https://api-codey.autohustle.online";
const API_KEY = process.env.CODEY_KEY || "";
const MAX_CONTEXT_CHARS = 100_000; // ~25K tokens

// ── Colors (no dependency needed) ────────────────────────────────────────────
const c = {
  green: (s: string) => `\x1b[32m${s}\x1b[0m`,
  red: (s: string) => `\x1b[31m${s}\x1b[0m`,
  yellow: (s: string) => `\x1b[33m${s}\x1b[0m`,
  cyan: (s: string) => `\x1b[36m${s}\x1b[0m`,
  dim: (s: string) => `\x1b[2m${s}\x1b[0m`,
  bold: (s: string) => `\x1b[1m${s}\x1b[0m`,
  magenta: (s: string) => `\x1b[35m${s}\x1b[0m`,
};

// ── File Discovery ───────────────────────────────────────────────────────────
const IGNORE_DIRS = new Set([
  "node_modules", ".git", ".next", "__pycache__", "dist", "build",
  ".venv", "venv", ".cache", ".turbo", "coverage", ".pytest_cache",
]);

const CODE_EXTS = new Set([
  ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb",
  ".php", ".css", ".html", ".json", ".yaml", ".yml", ".toml", ".sql",
  ".sh", ".md", ".txt", ".env.example", ".gitignore", ".dockerignore",
  "Dockerfile", "Makefile", "Procfile",
]);

function discoverFiles(dir: string, maxFiles = 50): { path: string; content: string }[] {
  const files: { path: string; content: string }[] = [];
  let totalChars = 0;

  function walk(current: string) {
    if (files.length >= maxFiles || totalChars >= MAX_CONTEXT_CHARS) return;

    let entries: string[];
    try {
      entries = readdirSync(current);
    } catch {
      return;
    }

    for (const entry of entries) {
      if (files.length >= maxFiles || totalChars >= MAX_CONTEXT_CHARS) return;

      const fullPath = join(current, entry);
      let stat;
      try {
        stat = statSync(fullPath);
      } catch {
        continue;
      }

      if (stat.isDirectory()) {
        if (!IGNORE_DIRS.has(entry) && !entry.startsWith(".")) {
          walk(fullPath);
        }
      } else if (stat.isFile()) {
        const ext = extname(entry);
        const isCode = CODE_EXTS.has(ext) || CODE_EXTS.has(entry);
        if (isCode && stat.size < 50_000) {
          try {
            const content = readFileSync(fullPath, "utf-8");
            const relPath = relative(dir, fullPath);
            files.push({ path: relPath, content });
            totalChars += content.length;
          } catch {
            // skip unreadable files
          }
        }
      }
    }
  }

  walk(dir);
  return files;
}

// ── CODEY.md Support ─────────────────────────────────────────────────────────
function readCodeyConfig(dir: string): string {
  const configPath = join(dir, "CODEY.md");
  if (existsSync(configPath)) {
    return readFileSync(configPath, "utf-8");
  }
  return "";
}

// ── API Streaming ────────────────────────────────────────────────────────────
async function streamGenerate(prompt: string, context: string, onToken: (token: string) => void): Promise<string> {
  const res = await fetch(`${API_BASE}/sessions/prompt/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${API_KEY}`,
    },
    body: JSON.stringify({ prompt, context, language: "auto" }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }

  const reader = res.body?.getReader();
  const decoder = new TextDecoder();
  let accumulated = "";

  if (reader) {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value, { stream: true });
      const lines = text.split("\n");

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "token") {
              accumulated += data.content;
              onToken(data.content);
            } else if (data.type === "error") {
              throw new Error(data.message);
            }
          } catch (e) {
            if (e instanceof SyntaxError) continue;
            throw e;
          }
        }
      }
    }
  }

  return accumulated;
}

// ── File Writer ──────────────────────────────────────────────────────────────
function applyFileChanges(output: string, cwd: string): string[] {
  const written: string[] = [];

  // Check for multi-file output: # filename: path
  const blockPattern = /```(?:\w+)?\n(?:#\s*filename:\s*(.+?)\n)?([\s\S]*?)```/g;
  let match;
  const blocks: { path: string; content: string }[] = [];

  while ((match = blockPattern.exec(output)) !== null) {
    const fname = match[1]?.trim();
    const content = match[2]?.trim();
    if (fname && content) {
      blocks.push({ path: fname, content });
    }
  }

  if (blocks.length > 0) {
    for (const block of blocks) {
      const fullPath = join(cwd, block.path);
      writeFileSync(fullPath, block.content + "\n");
      written.push(block.path);
    }
  }

  return written;
}

// ── Command Executor ─────────────────────────────────────────────────────────
function runCommand(cmd: string, cwd: string): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execSync(cmd, { cwd, encoding: "utf-8", timeout: 30_000, stdio: "pipe" });
    return { stdout, stderr: "", exitCode: 0 };
  } catch (e: any) {
    return {
      stdout: e.stdout || "",
      stderr: e.stderr || e.message || "",
      exitCode: e.status || 1,
    };
  }
}

// ── Interactive Chat Mode ────────────────────────────────────────────────────
async function chatMode(cwd: string) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const files = discoverFiles(cwd);
  const config = readCodeyConfig(cwd);

  console.log(c.bold(c.green("\n  CODEY") + " — AI Coding Agent"));
  console.log(c.dim(`  ${files.length} files indexed in ${cwd}`));
  if (config) console.log(c.dim("  CODEY.md config loaded"));
  console.log(c.dim("  Type your request. Use 'quit' to exit.\n"));

  const fileContext = files.map(f => `--- ${f.path} ---\n${f.content}`).join("\n\n");
  const fullContext = config ? `PROJECT CONFIG:\n${config}\n\n${fileContext}` : fileContext;
  const history: string[] = [];

  const prompt = () => {
    rl.question(c.green("codey> "), async (input) => {
      const trimmed = input.trim();
      if (!trimmed || trimmed === "quit" || trimmed === "exit") {
        console.log(c.dim("\nGoodbye."));
        rl.close();
        return;
      }

      history.push(trimmed);
      process.stdout.write(c.cyan("\n"));

      try {
        const output = await streamGenerate(
          trimmed,
          fullContext,
          (token) => process.stdout.write(token)
        );
        process.stdout.write("\x1b[0m\n");

        // Check if output contains file changes
        const written = applyFileChanges(output, cwd);
        if (written.length > 0) {
          console.log(c.green(`\n  ✓ Written ${written.length} file(s):`));
          for (const f of written) {
            console.log(c.dim(`    ${f}`));
          }
        }

        console.log();
      } catch (e: any) {
        console.log(c.red(`\n  Error: ${e.message}\n`));
      }

      prompt();
    });
  };

  prompt();
}

// ── Single Command Mode ──────────────────────────────────────────────────────
async function singleCommand(instruction: string, cwd: string) {
  const files = discoverFiles(cwd);
  const config = readCodeyConfig(cwd);

  console.log(c.bold(c.green("CODEY") + " — AI Coding Agent"));
  console.log(c.dim(`${files.length} files indexed`));
  console.log(c.dim(`Prompt: ${instruction}\n`));

  const fileContext = files.map(f => `--- ${f.path} ---\n${f.content}`).join("\n\n");
  const fullContext = config ? `PROJECT CONFIG:\n${config}\n\n${fileContext}` : fileContext;

  try {
    const output = await streamGenerate(
      instruction,
      fullContext,
      (token) => process.stdout.write(token)
    );
    process.stdout.write("\n\n");

    // Apply file changes
    const written = applyFileChanges(output, cwd);
    if (written.length > 0) {
      console.log(c.green(`✓ Written ${written.length} file(s):`));
      for (const f of written) {
        console.log(c.dim(`  ${f}`));
      }

      // Self-correcting loop: run tests if they exist
      console.log(c.dim("\nRunning tests..."));
      const testResult = detectAndRunTests(cwd);
      if (testResult) {
        if (testResult.exitCode === 0) {
          console.log(c.green("✓ Tests passed"));
        } else {
          console.log(c.red("✗ Tests failed:"));
          console.log(c.dim(testResult.stderr || testResult.stdout));

          // Auto-fix: send error back to API
          console.log(c.yellow("\nAttempting auto-fix..."));
          const fixOutput = await streamGenerate(
            `The code has test failures. Fix them.\n\nError:\n${testResult.stderr || testResult.stdout}\n\nReturn the COMPLETE fixed code.`,
            fullContext + "\n\nGENERATED CODE:\n" + output,
            (token) => process.stdout.write(token)
          );
          process.stdout.write("\n");

          const fixWritten = applyFileChanges(fixOutput, cwd);
          if (fixWritten.length > 0) {
            console.log(c.green(`✓ Applied fix to ${fixWritten.length} file(s)`));

            // Re-run tests
            const retest = detectAndRunTests(cwd);
            if (retest && retest.exitCode === 0) {
              console.log(c.green("✓ Tests pass after fix"));
            } else {
              console.log(c.yellow("⚠ Tests still failing — manual review needed"));
            }
          }
        }
      }
    }

    console.log();
  } catch (e: any) {
    console.log(c.red(`Error: ${e.message}`));
    process.exit(1);
  }
}

function detectAndRunTests(cwd: string): { stdout: string; stderr: string; exitCode: number } | null {
  // Detect test framework
  if (existsSync(join(cwd, "package.json"))) {
    const pkg = JSON.parse(readFileSync(join(cwd, "package.json"), "utf-8"));
    if (pkg.scripts?.test && pkg.scripts.test !== 'echo "Error: no test specified" && exit 1') {
      return runCommand("npm test", cwd);
    }
  }
  if (existsSync(join(cwd, "pytest.ini")) || existsSync(join(cwd, "setup.cfg")) || existsSync(join(cwd, "pyproject.toml"))) {
    return runCommand("python -m pytest --tb=short -q", cwd);
  }
  // Look for test files
  const testFiles = readdirSync(cwd).filter(f => f.startsWith("test_") || f.endsWith("_test.py"));
  if (testFiles.length > 0) {
    return runCommand("python -m pytest --tb=short -q", cwd);
  }
  return null;
}

// ── Analyze Command ──────────────────────────────────────────────────────────
async function analyzeCommand(cwd: string) {
  const files = discoverFiles(cwd);
  console.log(c.bold(c.green("CODEY") + " — Structural Analysis"));
  console.log(c.dim(`Scanning ${files.length} files...\n`));

  const output = await streamGenerate(
    "Analyze this codebase. Report: 1) Architecture overview, 2) Potential issues (complexity, coupling, security), 3) Recommendations. Be specific with file names and line references.",
    files.map(f => `--- ${f.path} ---\n${f.content}`).join("\n\n"),
    (token) => process.stdout.write(token)
  );
  console.log("\n");
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  const cwd = process.cwd();

  if (!API_KEY) {
    console.log(c.red("Error: CODEY_KEY environment variable not set."));
    console.log(c.dim("Get your API key at https://codey.autohustle.online/settings"));
    console.log(c.dim("Then: export CODEY_KEY=your_key_here"));
    process.exit(1);
  }

  if (args.length === 0 || args[0] === "chat") {
    await chatMode(cwd);
  } else if (args[0] === "analyze") {
    await analyzeCommand(cwd);
  } else if (args[0] === "fix") {
    await singleCommand("Find and fix bugs in this codebase. Focus on: error handling, edge cases, security issues.", cwd);
  } else if (args[0] === "test") {
    await singleCommand("Generate comprehensive tests for this codebase. Cover edge cases and error paths.", cwd);
  } else if (args[0] === "refactor") {
    await singleCommand("Refactor this codebase for better structure, readability, and maintainability.", cwd);
  } else if (args[0] === "--help" || args[0] === "-h") {
    console.log(`
${c.bold(c.green("CODEY"))} — AI Coding Agent

${c.bold("Usage:")}
  codey                     Interactive chat mode
  codey "instruction"       Execute a single instruction
  codey analyze             Structural analysis of your codebase
  codey fix                 Find and fix bugs
  codey test                Generate tests
  codey refactor            Refactor for better structure

${c.bold("Environment:")}
  CODEY_KEY                 Your API key (required)
  CODEY_API                 API base URL (default: https://api-codey.autohustle.online)

${c.bold("Config:")}
  Create a CODEY.md file in your project root with instructions
  for how Codey should work with your codebase.

${c.bold("Examples:")}
  codey "add authentication middleware to the Express routes"
  codey "convert this project from JavaScript to TypeScript"
  codey analyze
  codey fix
`);
  } else {
    // Treat all args as the instruction
    await singleCommand(args.join(" "), cwd);
  }
}

main().catch((e) => {
  console.error(c.red(`Fatal: ${e.message}`));
  process.exit(1);
});
