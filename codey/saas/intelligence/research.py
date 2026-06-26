from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "httpx":
        raise
    _HTTPX_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def _raise_missing_httpx(*args, **kwargs):
        raise RuntimeError("httpx is required for research network calls") from _HTTPX_IMPORT_ERROR

    class _MissingHTTPX:
        AsyncClient = staticmethod(_raise_missing_httpx)
        Timeout = staticmethod(lambda *args, **kwargs: None)

    httpx: Any = _MissingHTTPX()
else:  # pragma: no cover - depends on optional runtime dependency
    _HTTPX_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_URL_CREDENTIAL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+(?::[^/@\s]*)?@"
)
_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password)=)[^&\s]+"
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|token|secret|password|authorization)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?[\"']?)[^\"'\s,}&]+"
)
_EMAIL_ADDRESS_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)


def _require_httpx() -> None:
    if _HTTPX_IMPORT_ERROR is not None:
        raise RuntimeError("httpx is required for research network calls") from _HTTPX_IMPORT_ERROR


def _http_client():
    _require_httpx()
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_research_secret(name: str) -> str | None:
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None
    return normalized or None


def _redact_research_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _coerce_research_dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_research_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_research_text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _coerce_optional_research_text(value: object) -> str | None:
    text = _coerce_research_text(value)
    return text or None


def _coerce_research_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default


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
        self._tavily_key = _coerce_non_empty_research_secret("TAVILY_API_KEY")
        self._brave_key = _coerce_non_empty_research_secret("BRAVE_API_KEY")
        self._exa_key = _coerce_non_empty_research_secret("EXA_API_KEY")
        self._github_token = _coerce_non_empty_research_secret("GITHUB_TOKEN")

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
        async with _http_client() as client:
            resp = await client.get(f"https://pypi.org/pypi/{library}/json")
            if resp.status_code != 200:
                logger.warning(
                    "PyPI lookup failed for '%s': %d",
                    _redact_research_error(library),
                    resp.status_code,
                )
                return
            data = _coerce_research_dict(resp.json())
            pkg_info = _coerce_research_dict(data.get("info"))
            info.latest_version = _coerce_optional_research_text(pkg_info.get("version"))
            info.description = _coerce_optional_research_text(pkg_info.get("summary"))
            info.homepage = _coerce_optional_research_text(
                pkg_info.get("home_page")
            ) or _coerce_optional_research_text(pkg_info.get("project_url"))
            info.license = _coerce_optional_research_text(pkg_info.get("license"))

    async def _fetch_npm(self, library: str, info: LibraryInfo) -> None:
        """Fetch package metadata from npm."""
        async with _http_client() as client:
            resp = await client.get(f"https://registry.npmjs.org/{library}/latest")
            if resp.status_code != 200:
                logger.warning(
                    "npm lookup failed for '%s': %d",
                    _redact_research_error(library),
                    resp.status_code,
                )
                return
            data = _coerce_research_dict(resp.json())
            license_value = data.get("license")
            if isinstance(license_value, dict):
                license_value = license_value.get("type")
            info.latest_version = _coerce_optional_research_text(data.get("version"))
            info.description = _coerce_optional_research_text(data.get("description"))
            info.homepage = _coerce_optional_research_text(data.get("homepage"))
            info.license = _coerce_optional_research_text(license_value)

    async def _fetch_crates(self, library: str, info: LibraryInfo) -> None:
        """Fetch package metadata from crates.io."""
        async with _http_client() as client:
            resp = await client.get(
                f"https://crates.io/api/v1/crates/{library}",
                headers={"User-Agent": "codey-research/1.0"},
            )
            if resp.status_code != 200:
                logger.warning(
                    "crates.io lookup failed for '%s': %d",
                    _redact_research_error(library),
                    resp.status_code,
                )
                return
            data = _coerce_research_dict(resp.json())
            crate = _coerce_research_dict(data.get("crate"))
            versions = _coerce_research_dict_list(data.get("versions"))
            first_version = versions[0] if versions else {}
            info.latest_version = _coerce_optional_research_text(
                crate.get("max_version")
            ) or _coerce_optional_research_text(crate.get("newest_version"))
            info.description = _coerce_optional_research_text(crate.get("description"))
            info.homepage = _coerce_optional_research_text(
                crate.get("homepage")
            ) or _coerce_optional_research_text(crate.get("repository"))
            info.license = _coerce_optional_research_text(first_version.get("license"))

    async def _check_osv_vulns(
        self, library: str, ecosystem: str, info: LibraryInfo
    ) -> None:
        """Check OSV (open source vulnerabilities) database."""
        async with _http_client() as client:
            resp = await client.post(
                "https://api.osv.dev/v1/query",
                json={"package": {"name": library, "ecosystem": ecosystem}},
            )
            if resp.status_code != 200:
                logger.warning(
                    "OSV query failed for '%s': %d",
                    _redact_research_error(library),
                    resp.status_code,
                )
                return
            data = _coerce_research_dict(resp.json())
            vulns = _coerce_research_dict_list(data.get("vulns"))
            for vuln in vulns[:10]:  # Cap at 10
                info.vulnerabilities.append({
                    "id": _coerce_research_text(vuln.get("id")),
                    "summary": _coerce_research_text(vuln.get("summary")),
                    "severity": self._extract_severity(vuln),
                    "fixed": self._extract_fixed_version(vuln),
                })

    @staticmethod
    def _extract_severity(vuln: dict) -> str:
        """Extract severity from an OSV vulnerability record."""
        severity_list = _coerce_research_dict_list(vuln.get("severity"))
        if severity_list:
            return _coerce_research_text(severity_list[0].get("score"), "unknown")
        # Try database_specific
        db_specific = _coerce_research_dict(vuln.get("database_specific"))
        return _coerce_research_text(db_specific.get("severity"), "unknown")

    @staticmethod
    def _extract_fixed_version(vuln: dict) -> str | None:
        """Extract the earliest fixed version from an OSV record."""
        for affected in _coerce_research_dict_list(vuln.get("affected")):
            for rng in _coerce_research_dict_list(affected.get("ranges")):
                for event in _coerce_research_dict_list(rng.get("events")):
                    fixed = _coerce_optional_research_text(event.get("fixed"))
                    if fixed:
                        return fixed
        return None

    # -----------------------------------------------------------------------
    # Web search (Tavily → Brave → Exa fallback chain)
    # -----------------------------------------------------------------------

    async def search_web(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web, trying Tavily first, then Brave, then Exa."""
        if self._tavily_key:
            try:
                return await self._search_tavily(query, max_results)
            except Exception as exc:
                logger.warning(
                    "Tavily search failed, trying Brave: %s",
                    _redact_research_error(exc),
                )

        if self._brave_key:
            try:
                return await self._search_brave(query, max_results)
            except Exception as exc:
                logger.warning(
                    "Brave search failed, trying Exa: %s",
                    _redact_research_error(exc),
                )

        if self._exa_key:
            try:
                return await self._search_exa(query, max_results)
            except Exception as exc:
                logger.warning(
                    "Exa search failed: %s",
                    _redact_research_error(exc),
                )

        logger.warning("No search providers available (set TAVILY/BRAVE/EXA API keys)")
        return []

    async def _search_tavily(self, query: str, max_results: int) -> list[SearchResult]:
        async with _http_client() as client:
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
            results = data.get("results", []) if isinstance(data, dict) else []
            return [
                SearchResult(
                    title=_coerce_research_text(r.get("title")),
                    url=_coerce_research_text(r.get("url")),
                    snippet=_coerce_research_text(r.get("content"))[:500],
                    source="tavily",
                )
                for r in _coerce_research_dict_list(results)
            ]

    async def _search_brave(self, query: str, max_results: int) -> list[SearchResult]:
        async with _http_client() as client:
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
            web = data.get("web", {}) if isinstance(data, dict) else {}
            if not isinstance(web, dict):
                web = {}
            results = web.get("results", [])
            return [
                SearchResult(
                    title=_coerce_research_text(r.get("title")),
                    url=_coerce_research_text(r.get("url")),
                    snippet=_coerce_research_text(r.get("description"))[:500],
                    source="brave",
                )
                for r in _coerce_research_dict_list(results)
            ]

    async def _search_exa(self, query: str, max_results: int) -> list[SearchResult]:
        async with _http_client() as client:
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
                    title=_coerce_research_text(r.get("title")),
                    url=_coerce_research_text(r.get("url")),
                    snippet=(
                        _coerce_research_text(r.get("text"))
                        or _coerce_research_text(r.get("highlight"))
                    )[:500],
                    source="exa",
                )
                for r in _coerce_research_dict_list(
                    data.get("results", []) if isinstance(data, dict) else []
                )
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

        async with _http_client() as client:
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
        items = data.get("items", []) if isinstance(data, dict) else []
        for item in _coerce_research_dict_list(items):
            # Build snippet from text_matches
            snippets: list[str] = []
            for match in _coerce_research_dict_list(item.get("text_matches", [])):
                fragment = _coerce_research_text(match.get("fragment"))
                if fragment:
                    snippets.append(fragment)
            snippet = "\n---\n".join(snippets[:3]) if snippets else ""

            repo = item.get("repository", {})
            if not isinstance(repo, dict):
                repo = {}
            name = _coerce_research_text(item.get("name"))
            results.append(
                CodeSearchResult(
                    repo=_coerce_research_text(repo.get("full_name")),
                    path=_coerce_research_text(item.get("path")),
                    url=_coerce_research_text(item.get("html_url")),
                    snippet=snippet[:1000],
                    language=language or name.rsplit(".", 1)[-1],
                    stars=_coerce_research_int(repo.get("stargazers_count")),
                )
            )

        # Sort by stars descending
        results.sort(key=lambda r: r.stars, reverse=True)
        return results
