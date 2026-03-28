"""Unified intelligence services — every free API fused into one interface.

Covers all categories from the Codey Intelligence Services spec:
- Search (Tavily, Brave, Exa, Bing, Stack Overflow, Perplexity)
- Package Intelligence (PyPI, npm, crates.io, Maven, Packagist)
- Security (OSV.dev, Snyk, NVD/NIST)
- Code Analysis (GitHub code search, Semgrep)
- Documentation (DevDocs)
- LLM Providers (OpenAI-compatible multi-provider routing)
- Notifications (Discord, Slack webhooks)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

# Extension mapping for security scans
_EXT_MAP: dict[str, str] = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "rust": "rs",
    "go": "go",
    "java": "java",
    "php": "php",
    "ruby": "rb",
    "c": "c",
    "cpp": "cpp",
}

# OpenAI-compatible LLM provider registry
PROVIDERS: dict[str, dict[str, str]] = {
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key": "GEMINI_API_KEY",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "key": "GROQ_API_KEY",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "key": "OPENROUTER_API_KEY",
    },
    "mistral": {
        "base": "https://api.mistral.ai/v1",
        "key": "MISTRAL_API_KEY",
    },
    "cloudflare": {
        "base": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "key": "CLOUDFLARE_API_KEY",
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1",
        "key": "DEEPSEEK_API_KEY",
    },
    "together": {
        "base": "https://api.together.xyz/v1",
        "key": "TOGETHER_API_KEY",
    },
    "fireworks": {
        "base": "https://api.fireworks.ai/inference/v1",
        "key": "FIREWORKS_API_KEY",
    },
    "cerebras": {
        "base": "https://api.cerebras.ai/v1",
        "key": "CEREBRAS_API_KEY",
    },
}


class IntelligenceServices:
    """Manages all external intelligence sources.

    Every method gracefully handles missing API keys and network
    failures by returning ``None`` (or an empty collection).  The caller
    never has to worry about crashes from unconfigured services.
    """

    def __init__(self, *, timeout: float = 30) -> None:
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------

    async def search_tavily(
        self, query: str, *, max_results: int = 5
    ) -> list[dict] | None:
        """AI-optimised search via Tavily (1 000 searches/month free)."""
        key = os.getenv("TAVILY_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": max_results},
            )
            if resp.status_code == 200:
                return resp.json().get("results", [])
        except Exception:
            logger.debug("Tavily search failed for %r", query, exc_info=True)
        return None

    async def search_brave(
        self, query: str, *, count: int = 5
    ) -> list[dict] | None:
        """Independent web search via Brave (2 000 queries/month free)."""
        key = os.getenv("BRAVE_SEARCH_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": key},
                params={"q": query, "count": count},
            )
            if resp.status_code == 200:
                return resp.json().get("web", {}).get("results", [])
        except Exception:
            logger.debug("Brave search failed for %r", query, exc_info=True)
        return None

    async def search_exa(
        self, query: str, *, num_results: int = 5
    ) -> list[dict] | None:
        """Semantic search via Exa (1 000 searches/month free)."""
        key = os.getenv("EXA_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json={"query": query, "num_results": num_results},
            )
            if resp.status_code == 200:
                return resp.json().get("results", [])
        except Exception:
            logger.debug("Exa search failed for %r", query, exc_info=True)
        return None

    async def search_bing(
        self, query: str, *, count: int = 5
    ) -> list[dict] | None:
        """Web search via Bing/Azure (1 000 transactions/month free)."""
        key = os.getenv("BING_SEARCH_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": key},
                params={"q": query, "count": count},
            )
            if resp.status_code == 200:
                return resp.json().get("webPages", {}).get("value", [])
        except Exception:
            logger.debug("Bing search failed for %r", query, exc_info=True)
        return None

    async def search_perplexity(self, query: str) -> str | None:
        """Search + LLM answer via Perplexity ($5 free credits)."""
        key = os.getenv("PERPLEXITY_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": query}],
                },
            )
            if resp.status_code == 200:
                choices = resp.json().get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content")
        except Exception:
            logger.debug("Perplexity search failed for %r", query, exc_info=True)
        return None

    async def search_stackoverflow(
        self, query: str, *, pagesize: int = 5
    ) -> list[dict] | None:
        """Dev Q&A via Stack Overflow (10 000 req/day, no key needed)."""
        try:
            resp = await self._http.get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params={
                    "order": "desc",
                    "sort": "relevance",
                    "q": query,
                    "site": "stackoverflow",
                    "pagesize": pagesize,
                    "filter": "withbody",
                },
            )
            if resp.status_code == 200:
                return [
                    {
                        "title": item.get("title"),
                        "link": item.get("link"),
                        "score": item.get("score"),
                        "is_answered": item.get("is_answered"),
                    }
                    for item in resp.json().get("items", [])
                ]
        except Exception:
            logger.debug("Stack Overflow search failed for %r", query, exc_info=True)
        return None

    async def search_web(self, query: str) -> list[dict]:
        """Try all search providers in priority order, return first success."""
        for fn in [
            self.search_tavily,
            self.search_brave,
            self.search_exa,
            self.search_bing,
        ]:
            try:
                result = await fn(query)
                if result:
                    return result
            except Exception:
                continue
        return []

    # ------------------------------------------------------------------
    # PACKAGE INTELLIGENCE
    # ------------------------------------------------------------------

    async def get_pypi_info(self, package: str) -> dict | None:
        """Fetch Python package metadata from PyPI (unlimited, no auth)."""
        try:
            resp = await self._http.get(f"https://pypi.org/pypi/{package}/json")
            if resp.status_code == 200:
                info = resp.json()["info"]
                return {
                    "name": package,
                    "version": info["version"],
                    "summary": info.get("summary", ""),
                    "home_page": info.get("home_page") or info.get("project_url"),
                    "requires_python": info.get("requires_python"),
                    "license": info.get("license"),
                }
        except Exception:
            logger.debug("PyPI lookup failed for %r", package, exc_info=True)
        return None

    async def get_npm_info(self, package: str) -> dict | None:
        """Fetch Node.js package metadata from npm (unlimited, no auth)."""
        try:
            resp = await self._http.get(
                f"https://registry.npmjs.org/{package}/latest"
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "name": package,
                    "version": data.get("version"),
                    "description": data.get("description", ""),
                    "homepage": data.get("homepage"),
                    "repository": (data.get("repository") or {}).get("url"),
                }
        except Exception:
            logger.debug("npm lookup failed for %r", package, exc_info=True)
        return None

    async def get_crates_info(self, crate: str) -> dict | None:
        """Fetch Rust crate metadata from crates.io (unlimited, no auth)."""
        try:
            resp = await self._http.get(
                f"https://crates.io/api/v1/crates/{crate}",
                headers={"User-Agent": "codey-intelligence/1.0"},
            )
            if resp.status_code == 200:
                c = resp.json().get("crate", {})
                return {
                    "name": crate,
                    "version": c.get("newest_version"),
                    "description": c.get("description", ""),
                    "homepage": c.get("homepage"),
                    "repository": c.get("repository"),
                    "downloads": c.get("downloads"),
                }
        except Exception:
            logger.debug("crates.io lookup failed for %r", crate, exc_info=True)
        return None

    async def get_maven_info(self, group_id: str, artifact_id: str) -> dict | None:
        """Fetch Java/Kotlin package metadata from Maven Central (unlimited)."""
        try:
            resp = await self._http.get(
                "https://search.maven.org/solrsearch/select",
                params={
                    "q": f'g:"{group_id}" AND a:"{artifact_id}"',
                    "rows": 1,
                    "wt": "json",
                },
            )
            if resp.status_code == 200:
                docs = resp.json().get("response", {}).get("docs", [])
                if docs:
                    d = docs[0]
                    return {
                        "group_id": d.get("g"),
                        "artifact_id": d.get("a"),
                        "version": d.get("latestVersion"),
                        "timestamp": d.get("timestamp"),
                    }
        except Exception:
            logger.debug(
                "Maven lookup failed for %s:%s", group_id, artifact_id, exc_info=True
            )
        return None

    async def get_packagist_info(self, package: str) -> dict | None:
        """Fetch PHP Composer package metadata from Packagist (unlimited)."""
        try:
            resp = await self._http.get(
                f"https://packagist.org/packages/{package}.json"
            )
            if resp.status_code == 200:
                pkg = resp.json().get("package", {})
                versions = pkg.get("versions", {})
                latest_key = next(
                    (k for k in versions if not k.startswith("dev-")), None
                )
                latest = versions.get(latest_key, {}) if latest_key else {}
                return {
                    "name": package,
                    "version": latest.get("version"),
                    "description": pkg.get("description", ""),
                    "homepage": latest.get("homepage"),
                }
        except Exception:
            logger.debug("Packagist lookup failed for %r", package, exc_info=True)
        return None

    async def get_package_info(
        self, package: str, language: str
    ) -> dict | None:
        """Dispatch to the correct registry based on language."""
        dispatch: dict[str, Any] = {
            "python": lambda: self.get_pypi_info(package),
            "javascript": lambda: self.get_npm_info(package),
            "typescript": lambda: self.get_npm_info(package),
            "rust": lambda: self.get_crates_info(package),
            "php": lambda: self.get_packagist_info(package),
        }
        fn = dispatch.get(language)
        if fn:
            return await fn()
        return None

    # ------------------------------------------------------------------
    # SECURITY
    # ------------------------------------------------------------------

    async def check_osv(
        self,
        package: str,
        version: str,
        ecosystem: str = "PyPI",
    ) -> list[dict]:
        """Check a package against OSV.dev (unlimited, no auth)."""
        try:
            resp = await self._http.post(
                "https://api.osv.dev/v1/query",
                json={
                    "package": {"name": package, "ecosystem": ecosystem},
                    "version": version,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("vulns", [])
        except Exception:
            logger.debug(
                "OSV check failed for %s@%s", package, version, exc_info=True
            )
        return []

    async def check_nvd(self, cve_id: str) -> dict | None:
        """Look up a CVE in the NVD/NIST database (free key, high limits)."""
        key = os.getenv("NVD_API_KEY")
        headers: dict[str, str] = {}
        if key:
            headers["apiKey"] = key
        try:
            resp = await self._http.get(
                "https://services.nvd.nist.gov/rest/json/cves/2.0",
                headers=headers,
                params={"cveId": cve_id},
            )
            if resp.status_code == 200:
                vulns = resp.json().get("vulnerabilities", [])
                if vulns:
                    return vulns[0].get("cve", {})
        except Exception:
            logger.debug("NVD lookup failed for %r", cve_id, exc_info=True)
        return None

    async def check_snyk(self, packages: list[dict]) -> list[dict]:
        """Check packages against Snyk vulnerability database (200 tests/month free).

        *packages* should be a list of ``{"name": ..., "version": ...}`` dicts.
        """
        key = os.getenv("SNYK_API_KEY")
        if not key:
            return []
        try:
            resp = await self._http.post(
                "https://api.snyk.io/v1/test/pip",
                headers={"Authorization": f"token {key}"},
                json={"packages": packages},
            )
            if resp.status_code == 200:
                vulns = (
                    resp.json()
                    .get("issues", {})
                    .get("vulnerabilities", [])
                )
                return [
                    {
                        "package": v.get("package"),
                        "severity": v.get("severity"),
                        "title": v.get("title"),
                        "fix": v.get("fixedIn", "No fix"),
                    }
                    for v in vulns
                ]
        except Exception:
            logger.debug("Snyk check failed", exc_info=True)
        return []

    async def check_package_security(
        self,
        package: str,
        version: str,
        language: str,
    ) -> dict:
        """Unified security check combining OSV + Snyk for one package."""
        ecosystem_map = {
            "python": "PyPI",
            "javascript": "npm",
            "typescript": "npm",
            "rust": "crates.io",
            "go": "Go",
            "java": "Maven",
            "php": "Packagist",
        }
        ecosystem = ecosystem_map.get(language, "PyPI")

        osv_vulns, snyk_vulns = await asyncio.gather(
            self.check_osv(package, version, ecosystem),
            self.check_snyk([{"name": package, "version": version}]),
            return_exceptions=True,
        )
        if isinstance(osv_vulns, BaseException):
            osv_vulns = []
        if isinstance(snyk_vulns, BaseException):
            snyk_vulns = []

        osv_details = [
            {"id": v.get("id"), "summary": v.get("summary", ""), "source": "osv"}
            for v in osv_vulns[:5]
        ]
        snyk_details = [
            {"id": v.get("title", ""), "summary": v.get("severity", ""), "source": "snyk"}
            for v in snyk_vulns[:5]
        ]

        total = len(osv_vulns) + len(snyk_vulns)
        return {
            "package": package,
            "version": version,
            "vulnerabilities": total,
            "details": osv_details + snyk_details,
        }

    # ------------------------------------------------------------------
    # CODE ANALYSIS — Semgrep
    # ------------------------------------------------------------------

    async def semgrep_scan(self, code: str, language: str) -> list[dict]:
        """Run Semgrep SAST on a code snippet (free CLI, unlimited scans).

        Returns a list of findings with rule, severity, message, and line.
        Returns an empty list if Semgrep is not installed or fails.
        """
        ext = _EXT_MAP.get(language, "py")
        tmp_path = os.path.join(tempfile.gettempdir(), f"codey_scan_{uuid4()}.{ext}")
        try:
            with open(tmp_path, "w") as f:
                f.write(code)
            result = subprocess.run(
                ["semgrep", "--config", "auto", "--json", "--quiet", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            findings = json.loads(result.stdout).get("results", [])
            return [
                {
                    "rule": finding["check_id"],
                    "severity": finding.get("extra", {}).get("severity", "unknown"),
                    "message": finding.get("extra", {}).get("message", ""),
                    "line": finding.get("start", {}).get("line"),
                }
                for finding in findings
            ]
        except FileNotFoundError:
            logger.debug("Semgrep not installed, skipping scan")
        except subprocess.TimeoutExpired:
            logger.warning("Semgrep scan timed out")
        except Exception:
            logger.debug("Semgrep scan failed", exc_info=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return []

    # ------------------------------------------------------------------
    # GITHUB CODE SEARCH
    # ------------------------------------------------------------------

    async def search_github_code(
        self,
        query: str,
        *,
        language: str | None = None,
        per_page: int = 5,
    ) -> list[dict]:
        """Search all of GitHub for code examples (5 000 req/hour free)."""
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_CLIENT_SECRET")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        q = f"{query} language:{language}" if language else query
        try:
            resp = await self._http.get(
                "https://api.github.com/search/code",
                headers=headers,
                params={"q": q, "per_page": per_page},
            )
            if resp.status_code == 200:
                return [
                    {
                        "repo": item["repository"]["full_name"],
                        "path": item["path"],
                        "url": item["html_url"],
                    }
                    for item in resp.json().get("items", [])
                ]
        except Exception:
            logger.debug("GitHub code search failed for %r", query, exc_info=True)
        return []

    async def search_github_repos(
        self,
        query: str,
        *,
        per_page: int = 5,
    ) -> list[dict]:
        """Search GitHub repositories."""
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_CLIENT_SECRET")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        try:
            resp = await self._http.get(
                "https://api.github.com/search/repositories",
                headers=headers,
                params={"q": query, "per_page": per_page, "sort": "stars"},
            )
            if resp.status_code == 200:
                return [
                    {
                        "full_name": r["full_name"],
                        "description": r.get("description", ""),
                        "stars": r.get("stargazers_count", 0),
                        "url": r["html_url"],
                    }
                    for r in resp.json().get("items", [])
                ]
        except Exception:
            logger.debug("GitHub repo search failed for %r", query, exc_info=True)
        return []

    # ------------------------------------------------------------------
    # DOCUMENTATION
    # ------------------------------------------------------------------

    async def fetch_devdocs(self, library: str) -> str | None:
        """Fetch documentation index from DevDocs (free, no key)."""
        try:
            resp = await self._http.get(
                f"https://devdocs.io/api/entries/{library}"
            )
            if resp.status_code == 200:
                entries = resp.json()
                if entries:
                    return "\n".join(
                        e.get("name", "") for e in entries[:20]
                    )
        except Exception:
            logger.debug("DevDocs fetch failed for %r", library, exc_info=True)
        return None

    async def fetch_libraries_io(self, package: str, platform: str = "pypi") -> dict | None:
        """Cross-ecosystem dependency intelligence via Libraries.io (free key)."""
        key = os.getenv("LIBRARIES_IO_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.get(
                f"https://libraries.io/api/{platform}/{package}",
                params={"api_key": key},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "name": data.get("name"),
                    "platform": data.get("platform"),
                    "latest_version": data.get("latest_release_number"),
                    "dependents_count": data.get("dependents_count"),
                    "rank": data.get("rank"),
                    "homepage": data.get("homepage"),
                }
        except Exception:
            logger.debug("Libraries.io lookup failed for %r", package, exc_info=True)
        return None

    # ------------------------------------------------------------------
    # LLM PROVIDER COMPLETION (OpenAI-compatible)
    # ------------------------------------------------------------------

    async def llm_complete(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str | None:
        """Send a chat completion to any OpenAI-compatible provider.

        Returns the assistant message content, or ``None`` on failure.
        """
        cfg = PROVIDERS.get(provider)
        if not cfg:
            logger.warning("Unknown LLM provider: %s", provider)
            return None

        api_key = os.getenv(cfg["key"])
        if not api_key:
            return None

        base = cfg["base"]
        # Handle Cloudflare account-id substitution
        if provider == "cloudflare":
            account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
            if not account_id:
                return None
            base = base.replace("{account_id}", account_id)

        url = f"{base.rstrip('/')}/chat/completions"
        try:
            resp = await self._http.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 200:
                choices = resp.json().get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content")
        except Exception:
            logger.debug(
                "LLM completion failed for %s/%s", provider, model, exc_info=True
            )
        return None

    # ------------------------------------------------------------------
    # NOTIFICATIONS
    # ------------------------------------------------------------------

    async def notify_discord(self, content: str) -> bool:
        """Send a message via Discord webhook (free)."""
        url = os.getenv("DISCORD_WEBHOOK_URL")
        if not url:
            return False
        try:
            resp = await self._http.post(url, json={"content": content})
            return resp.status_code in (200, 204)
        except Exception:
            logger.debug("Discord notification failed", exc_info=True)
        return False

    async def notify_slack(self, text: str) -> bool:
        """Send a message via Slack incoming webhook (free)."""
        url = os.getenv("SLACK_WEBHOOK_URL")
        if not url:
            return False
        try:
            resp = await self._http.post(url, json={"text": text})
            return resp.status_code == 200
        except Exception:
            logger.debug("Slack notification failed", exc_info=True)
        return False

    # ------------------------------------------------------------------
    # UNIFIED RESEARCH — runs all relevant sources in parallel
    # ------------------------------------------------------------------

    async def research_for_task(
        self,
        prompt: str,
        language: str = "python",
    ) -> dict:
        """Run all relevant research for a coding task in parallel.

        Queries web search, package registries, Stack Overflow, GitHub,
        and documentation simultaneously, then returns a merged result dict.
        """
        libraries = self._extract_libraries(prompt)

        coros: dict[str, Any] = {
            "web": self.search_web(f"{prompt} {language} tutorial example"),
            "stackoverflow": self.search_stackoverflow(
                f"{prompt} {language}"
            ),
            "github": self.search_github_code(prompt, language=language),
        }

        for lib in libraries[:3]:
            coros[f"pkg_{lib}"] = self.get_package_info(lib, language)
            coros[f"docs_{lib}"] = self.fetch_devdocs(lib)

        results: dict[str, Any] = {}
        gathered = await asyncio.gather(
            *coros.values(), return_exceptions=True
        )
        for key, value in zip(coros.keys(), gathered):
            results[key] = None if isinstance(value, BaseException) else value

        return results

    async def package_intelligence(
        self, packages: list[str], language: str
    ) -> dict:
        """Full package intelligence pipeline: version + vulns + docs + examples."""

        async def _gather_one(pkg: str) -> tuple[str, dict]:
            info, vulns, docs, examples = await asyncio.gather(
                self.get_package_info(pkg, language),
                self.check_osv(
                    pkg,
                    "latest",
                    {
                        "python": "PyPI",
                        "javascript": "npm",
                        "rust": "crates.io",
                    }.get(language, "PyPI"),
                ),
                self.fetch_devdocs(pkg),
                self.search_github_code(
                    f"{pkg} example", language=language
                ),
                return_exceptions=True,
            )
            return pkg, {
                "info": None if isinstance(info, BaseException) else info,
                "vulns": [] if isinstance(vulns, BaseException) else vulns,
                "docs": None if isinstance(docs, BaseException) else docs,
                "examples": [] if isinstance(examples, BaseException) else examples,
            }

        results = await asyncio.gather(
            *(_gather_one(p) for p in packages), return_exceptions=True
        )
        return {
            pkg: data
            for r in results
            if not isinstance(r, BaseException)
            for pkg, data in [r]
        }

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_libraries(text: str) -> list[str]:
        """Extract potential library names from natural-language text."""
        patterns = [
            r"(?:using|with|import|install|add|require)\s+(\w[\w.-]*)",
            r"(\w[\w.-]*)\s+(?:library|package|module|framework|crate)",
        ]
        libs: set[str] = set()
        for pattern in patterns:
            libs.update(re.findall(pattern, text.lower()))

        stopwords = {
            "the", "a", "an", "and", "or", "to", "from", "in", "for",
            "all", "my", "this", "that", "be", "is", "are", "was",
            "it", "of", "on", "at", "as", "by", "so", "if", "do",
            "no", "not", "but", "up", "out", "new", "also", "can",
        }
        return [lib for lib in libs if lib not in stopwords and len(lib) > 2]

    @staticmethod
    def available_providers() -> list[str]:
        """Return the list of LLM providers that have keys configured."""
        return [
            name
            for name, cfg in PROVIDERS.items()
            if os.getenv(cfg["key"])
        ]


# Module-level singleton — import and use directly.
intelligence_services = IntelligenceServices()
