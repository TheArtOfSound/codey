from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LibraryInfo:
    name: str
    latest_version: str | None = None
    description: str | None = None
    homepage: str | None = None
    license: str | None = None
    vulnerabilities: list[dict[str, str]] = field(default_factory=list)
    doc_snippets: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str  # "tavily", "brave", "exa", "github"


@dataclass
class CodeSearchResult:
    repo: str
    path: str
    url: str
    snippet: str
    language: str
    stars: int = 0


# ---------------------------------------------------------------------------
# Research Engine
# ---------------------------------------------------------------------------


class ResearchEngine:
    """Web intelligence: library research, web search, code search."""

    def __init__(self) -> None:
        self._tavily_key = os.environ.get("TAVILY_API_KEY")
        self._brave_key = os.environ.get("BRAVE_API_KEY")
        self._exa_key = os.environ.get("EXA_API_KEY")
        self._github_token = os.environ.get("GITHUB_TOKEN")

    # -----------------------------------------------------------------------
    # Library research
    # -----------------------------------------------------------------------

    async def research_library(
        self, library: str, language: str = "python"
    ) -> LibraryInfo:
        """Fetch latest version, vulnerabilities, and docs for *library*."""
        info = LibraryInfo(name=library)

        tasks = []
        if language == "python":
            tasks.append(self._fetch_pypi(library, info))
            tasks.append(self._check_osv_vulns(library, "PyPI", info))
        elif language in ("javascript", "typescript"):
            tasks.append(self._fetch_npm(library, info))
            tasks.append(self._check_osv_vulns(library, "npm", info))
        elif language == "rust":
            tasks.append(self._fetch_crates(library, info))
            tasks.append(self._check_osv_vulns(library, "crates.io", info))

        await asyncio.gather(*tasks, return_exceptions=True)
        return info

    async def _fetch_pypi(self, library: str, info: LibraryInfo) -> None:
        """Fetch package metadata from PyPI."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(f"https://pypi.org/pypi/{library}/json")
            if resp.status_code != 200:
                logger.warning("PyPI lookup failed for '%s': %d", library, resp.status_code)
                return
            data = resp.json()
            pkg_info = data.get("info", {})
            info.latest_version = pkg_info.get("version")
            info.description = pkg_info.get("summary")
            info.homepage = pkg_info.get("home_page") or pkg_info.get("project_url")
            info.license = pkg_info.get("license")

    async def _fetch_npm(self, library: str, info: LibraryInfo) -> None:
        """Fetch package metadata from npm."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(f"https://registry.npmjs.org/{library}/latest")
            if resp.status_code != 200:
                logger.warning("npm lookup failed for '%s': %d", library, resp.status_code)
                return
            data = resp.json()
            info.latest_version = data.get("version")
            info.description = data.get("description")
            info.homepage = data.get("homepage")
            info.license = data.get("license")

    async def _fetch_crates(self, library: str, info: LibraryInfo) -> None:
        """Fetch package metadata from crates.io."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"https://crates.io/api/v1/crates/{library}",
                headers={"User-Agent": "codey-research/1.0"},
            )
            if resp.status_code != 200:
                logger.warning("crates.io lookup failed for '%s': %d", library, resp.status_code)
                return
            data = resp.json()
            crate = data.get("crate", {})
            info.latest_version = crate.get("max_version") or crate.get("newest_version")
            info.description = crate.get("description")
            info.homepage = crate.get("homepage") or crate.get("repository")
            info.license = data.get("versions", [{}])[0].get("license") if data.get("versions") else None

    async def _check_osv_vulns(
        self, library: str, ecosystem: str, info: LibraryInfo
    ) -> None:
        """Check OSV (open source vulnerabilities) database."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.osv.dev/v1/query",
                json={"package": {"name": library, "ecosystem": ecosystem}},
            )
            if resp.status_code != 200:
                logger.warning("OSV query failed for '%s': %d", library, resp.status_code)
                return
            data = resp.json()
            vulns = data.get("vulns", [])
            for vuln in vulns[:10]:  # Cap at 10
                info.vulnerabilities.append({
                    "id": vuln.get("id", ""),
                    "summary": vuln.get("summary", ""),
                    "severity": self._extract_severity(vuln),
                    "fixed": self._extract_fixed_version(vuln),
                })

    @staticmethod
    def _extract_severity(vuln: dict) -> str:
        """Extract severity from an OSV vulnerability record."""
        severity_list = vuln.get("severity", [])
        if severity_list:
            return severity_list[0].get("score", "unknown")
        # Try database_specific
        db_specific = vuln.get("database_specific", {})
        return db_specific.get("severity", "unknown")

    @staticmethod
    def _extract_fixed_version(vuln: dict) -> str | None:
        """Extract the earliest fixed version from an OSV record."""
        for affected in vuln.get("affected", []):
            for rng in affected.get("ranges", []):
                for event in rng.get("events", []):
                    if "fixed" in event:
                        return event["fixed"]
        return None

    # -----------------------------------------------------------------------
    # Web search (Tavily → Brave → Exa fallback chain)
    # -----------------------------------------------------------------------

    async def search_web(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web, trying Tavily first, then Brave, then Exa."""
        if self._tavily_key:
            try:
                return await self._search_tavily(query, max_results)
            except Exception:
                logger.exception("Tavily search failed, trying Brave")

        if self._brave_key:
            try:
                return await self._search_brave(query, max_results)
            except Exception:
                logger.exception("Brave search failed, trying Exa")

        if self._exa_key:
            try:
                return await self._search_exa(query, max_results)
            except Exception:
                logger.exception("Exa search failed")

        logger.warning("No search providers available (set TAVILY/BRAVE/EXA API keys)")
        return []

    async def _search_tavily(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self._tavily_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", "")[:500],
                    source="tavily",
                )
                for r in data.get("results", [])
            ]

    async def _search_brave(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={
                    "X-Subscription-Token": self._brave_key,
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("web", {}).get("results", [])
            return [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("description", "")[:500],
                    source="brave",
                )
                for r in results
            ]

    async def _search_exa(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                json={
                    "query": query,
                    "num_results": max_results,
                    "use_autoprompt": True,
                },
                headers={
                    "x-api-key": self._exa_key,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("text", r.get("highlight", ""))[:500],
                    source="exa",
                )
                for r in data.get("results", [])
            ]

    # -----------------------------------------------------------------------
    # Code search (GitHub)
    # -----------------------------------------------------------------------

    async def search_code(
        self, query: str, language: str | None = None, max_results: int = 10
    ) -> list[CodeSearchResult]:
        """Search GitHub for code examples matching *query*."""
        if not self._github_token:
            logger.warning("GITHUB_TOKEN not set — code search unavailable")
            return []

        search_query = query
        if language:
            search_query += f" language:{language}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.github.com/search/code",
                params={"q": search_query, "per_page": max_results, "sort": "indexed"},
                headers={
                    "Authorization": f"Bearer {self._github_token}",
                    "Accept": "application/vnd.github.text-match+json",
                },
            )
            if resp.status_code == 403:
                logger.warning("GitHub code search rate-limited")
                return []
            resp.raise_for_status()
            data = resp.json()

        results: list[CodeSearchResult] = []
        for item in data.get("items", []):
            # Build snippet from text_matches
            snippets: list[str] = []
            for match in item.get("text_matches", []):
                fragment = match.get("fragment", "")
                if fragment:
                    snippets.append(fragment)
            snippet = "\n---\n".join(snippets[:3]) if snippets else ""

            repo = item.get("repository", {})
            results.append(
                CodeSearchResult(
                    repo=repo.get("full_name", ""),
                    path=item.get("path", ""),
                    url=item.get("html_url", ""),
                    snippet=snippet[:1000],
                    language=language or item.get("name", "").rsplit(".", 1)[-1],
                    stars=repo.get("stargazers_count", 0),
                )
            )

        # Sort by stars descending
        results.sort(key=lambda r: r.stars, reverse=True)
        return results
