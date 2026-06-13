"""Unified intelligence services — every free API fused into one interface.

Covers all categories from the Codey Intelligence Services spec:
- Search (Tavily, Brave, Exa, Bing, Stack Overflow, Perplexity)
- Package Intelligence (PyPI, npm, crates.io, Maven, Packagist)
- Security (OSV.dev, Snyk, NVD/NIST, Semgrep, SonarCloud, Aikido, DeepSource)
- Code Analysis (GitHub code search, Semgrep)
- Documentation (DevDocs, Libraries.io)
- LLM Providers (OpenAI-compatible multi-provider routing)
- Monitoring (BetterStack, UptimeRobot)
- Dev Tooling (Linear, Vercel, Railway)
- Communication (Discord, Slack, Twilio SMS)
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import tempfile
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - exercised via import hook
    if exc.name != "httpx":
        raise
    _HTTPX_IMPORT_ERROR = exc

    def _raise_missing_httpx(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "httpx is required for intelligence service network calls"
        ) from _HTTPX_IMPORT_ERROR

    class _MissingHTTPX:
        AsyncClient = staticmethod(_raise_missing_httpx)

    httpx: Any = _MissingHTTPX()
else:
    _HTTPX_IMPORT_ERROR = None

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
    "huggingface": {
        "base": "https://api-inference.huggingface.co/v1",
        "key": "HUGGINGFACE_API_KEY",
    },
    "cohere": {
        "base": "https://api.cohere.ai/v1",
        "key": "COHERE_API_KEY",
    },
}

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
_SERVICE_DRAIN_TIMEOUT_SECONDS = 5.0


def _redact_service_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)


def _coerce_non_empty_service_secret(name: str) -> str | None:
    value = os.getenv(name)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return None
    if any(char.isspace() for char in normalized):
        return None
    return normalized or None


def _coerce_service_host_ip(
    hostname: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    if hostname.isdecimal():
        numeric_host = int(hostname, 10)
        if 0 <= numeric_host <= 0xFFFFFFFF:
            try:
                return ipaddress.ip_address(numeric_host)
            except ValueError:
                return None
    return None


def _coerce_obfuscated_service_ipv4(hostname: str) -> ipaddress.IPv4Address | None:
    parts = hostname.split(".")
    if not 1 <= len(parts) <= 4:
        return None

    values: list[int] = []
    for part in parts:
        if not part:
            return None
        base = 10
        digits = part
        if part.lower().startswith("0x"):
            base = 16
            digits = part[2:]
        elif len(part) > 1 and part.startswith("0"):
            base = 8
        if not digits:
            return None
        try:
            values.append(int(part, base))
        except ValueError:
            return None

    if len(values) == 1:
        if values[0] > 0xFFFFFFFF:
            return None
        address = values[0]
    elif len(values) == 2:
        if values[0] > 0xFF or values[1] > 0xFFFFFF:
            return None
        address = (values[0] << 24) | values[1]
    elif len(values) == 3:
        if values[0] > 0xFF or values[1] > 0xFF or values[2] > 0xFFFF:
            return None
        address = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        if any(value > 0xFF for value in values):
            return None
        address = (
            (values[0] << 24)
            | (values[1] << 16)
            | (values[2] << 8)
            | values[3]
        )
    return ipaddress.IPv4Address(address)


def _coerce_service_webhook_url(name: str) -> str | None:
    url = _coerce_non_empty_service_secret(name)
    if not url:
        return None

    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and port <= 0:
        return None

    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        return None

    host_ip = _coerce_service_host_ip(hostname)
    if host_ip is None:
        host_ip = _coerce_obfuscated_service_ipv4(hostname)
    if host_ip is not None and (
        host_ip.is_loopback
        or host_ip.is_private
        or host_ip.is_link_local
        or host_ip.is_multicast
        or host_ip.is_reserved
        or host_ip.is_unspecified
    ):
        return None

    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, "")
    )


def _semgrep_extension(language: object) -> str:
    if not isinstance(language, str):
        return "py"
    normalized = language.strip().lower()
    if not normalized or any(
        ord(char) < 32 or ord(char) == 127 for char in normalized
    ):
        return "py"
    return _EXT_MAP.get(normalized, "py")


def _require_httpx() -> None:
    if _HTTPX_IMPORT_ERROR is not None:
        raise RuntimeError(
            "httpx is required for intelligence service network calls"
        ) from _HTTPX_IMPORT_ERROR


async def _terminate_service_process(
    proc: asyncio.subprocess.Process,
    process_name: str,
) -> None:
    if proc.returncode is not None:
        return

    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except Exception as exc:
        logger.warning(
            "Failed to kill timed-out %s process: %s",
            _redact_service_error(process_name),
            _redact_service_error(exc),
        )
        return

    try:
        await asyncio.wait_for(
            proc.communicate(),
            timeout=_SERVICE_DRAIN_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "Failed to drain timed-out %s process: %s",
            _redact_service_error(process_name),
            _redact_service_error(exc),
        )


def _decode_process_output(payload: object) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    return ""


def _coerce_semgrep_text(value: object, default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip()
    return normalized or default


def _coerce_semgrep_line(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _normalize_semgrep_findings(payload: object) -> list[dict]:
    if not isinstance(payload, list):
        return []

    normalized: list[dict] = []
    for finding in payload:
        if not isinstance(finding, dict):
            continue
        rule = finding.get("check_id")
        if not isinstance(rule, str) or not rule.strip():
            continue
        extra = finding.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        start = finding.get("start")
        if not isinstance(start, dict):
            start = {}
        severity = _coerce_semgrep_text(extra.get("severity"), "unknown")
        message = _coerce_semgrep_text(extra.get("message"), "")
        line = _coerce_semgrep_line(start.get("line"))
        normalized.append(
            {
                "rule": rule.strip(),
                "severity": severity,
                "message": message,
                "line": line,
            }
        )
    return normalized


def _coerce_dict_list(payload: object) -> list[dict]:
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _coerce_repository_url(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        url = payload.get("url")
        return url if isinstance(url, str) else None
    return None


def _coerce_pypi_home_page(info: dict) -> str | None:
    home_page = info.get("home_page")
    if isinstance(home_page, str) and home_page:
        return home_page
    project_url = info.get("project_url")
    if isinstance(project_url, str) and project_url:
        return project_url
    project_urls = info.get("project_urls")
    if isinstance(project_urls, dict):
        for key in ("Homepage", "Home", "Source", "Repository"):
            value = project_urls.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _coerce_llm_content(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _quote_service_path_segment(value: object) -> str:
    return quote(str(value), safe="")


class IntelligenceServices:
    """Manages all external intelligence sources.

    Every method gracefully handles missing API keys and network
    failures by returning ``None`` (or an empty collection).  The caller
    never has to worry about crashes from unconfigured services.
    """

    def __init__(self, *, timeout: float = 30) -> None:
        self._timeout = timeout
        self._http_client: Any | None = None

    @property
    def _http(self) -> Any:
        if self._http_client is None:
            _require_httpx()
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        http_client = self._http_client
        self._http_client = None
        if http_client is not None:
            await http_client.aclose()

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------

    async def search_tavily(
        self, query: str, *, max_results: int = 5
    ) -> list[dict] | None:
        """AI-optimised search via Tavily (1 000 searches/month free)."""
        key = _coerce_non_empty_service_secret("TAVILY_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": max_results},
            )
            if resp.status_code == 200:
                payload = resp.json()
                results = payload.get("results", []) if isinstance(payload, dict) else []
                return _coerce_dict_list(results)
        except Exception as exc:
            logger.debug(
                "Tavily search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
        return None

    async def search_brave(
        self, query: str, *, count: int = 5
    ) -> list[dict] | None:
        """Independent web search via Brave (2 000 queries/month free)."""
        key = _coerce_non_empty_service_secret("BRAVE_SEARCH_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": key},
                params={"q": query, "count": count},
            )
            if resp.status_code == 200:
                payload = resp.json()
                web = payload.get("web") if isinstance(payload, dict) else None
                results = web.get("results", []) if isinstance(web, dict) else []
                return _coerce_dict_list(results)
        except Exception as exc:
            logger.debug(
                "Brave search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
        return None

    async def search_exa(
        self, query: str, *, num_results: int = 5
    ) -> list[dict] | None:
        """Semantic search via Exa (1 000 searches/month free)."""
        key = _coerce_non_empty_service_secret("EXA_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json={"query": query, "num_results": num_results},
            )
            if resp.status_code == 200:
                payload = resp.json()
                results = payload.get("results", []) if isinstance(payload, dict) else []
                return _coerce_dict_list(results)
        except Exception as exc:
            logger.debug(
                "Exa search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
        return None

    async def search_bing(
        self, query: str, *, count: int = 5
    ) -> list[dict] | None:
        """Web search via Bing/Azure (1 000 transactions/month free)."""
        key = _coerce_non_empty_service_secret("BING_SEARCH_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": key},
                params={"q": query, "count": count},
            )
            if resp.status_code == 200:
                payload = resp.json()
                web_pages = payload.get("webPages") if isinstance(payload, dict) else None
                results = web_pages.get("value", []) if isinstance(web_pages, dict) else []
                return _coerce_dict_list(results)
        except Exception as exc:
            logger.debug(
                "Bing search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
        return None

    async def search_perplexity(self, query: str) -> str | None:
        """Search + LLM answer via Perplexity ($5 free credits)."""
        key = _coerce_non_empty_service_secret("PERPLEXITY_API_KEY")
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
                return _coerce_llm_content(resp.json())
        except Exception as exc:
            logger.debug(
                "Perplexity search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
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
                payload = resp.json()
                items = payload.get("items", []) if isinstance(payload, dict) else []
                return [
                    {
                        "title": item.get("title"),
                        "link": item.get("link"),
                        "score": item.get("score"),
                        "is_answered": item.get("is_answered"),
                    }
                    for item in _coerce_dict_list(items)
                ]
        except Exception as exc:
            logger.debug(
                "Stack Overflow search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
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
                if isinstance(result, list) and result:
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
                payload = resp.json()
                info = payload.get("info") if isinstance(payload, dict) else None
                if not isinstance(info, dict):
                    return None
                version = info.get("version")
                if not isinstance(version, str) or not version:
                    return None
                return {
                    "name": package,
                    "version": version,
                    "summary": info.get("summary", ""),
                    "home_page": _coerce_pypi_home_page(info),
                    "requires_python": info.get("requires_python"),
                    "license": info.get("license"),
                }
        except Exception as exc:
            logger.debug(
                "PyPI lookup failed for %r: %s",
                _redact_service_error(package),
                _redact_service_error(exc),
            )
        return None

    async def get_npm_info(self, package: str) -> dict | None:
        """Fetch Node.js package metadata from npm (unlimited, no auth)."""
        try:
            resp = await self._http.get(
                f"https://registry.npmjs.org/{package}/latest"
            )
            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, dict):
                    return None
                return {
                    "name": package,
                    "version": data.get("version"),
                    "description": data.get("description", ""),
                    "homepage": data.get("homepage"),
                    "repository": _coerce_repository_url(data.get("repository")),
                }
        except Exception as exc:
            logger.debug(
                "npm lookup failed for %r: %s",
                _redact_service_error(package),
                _redact_service_error(exc),
            )
        return None

    async def get_crates_info(self, crate: str) -> dict | None:
        """Fetch Rust crate metadata from crates.io (unlimited, no auth)."""
        try:
            resp = await self._http.get(
                f"https://crates.io/api/v1/crates/{crate}",
                headers={"User-Agent": "codey-intelligence/1.0"},
            )
            if resp.status_code == 200:
                payload = resp.json()
                c = payload.get("crate") if isinstance(payload, dict) else None
                if not isinstance(c, dict):
                    return None
                return {
                    "name": crate,
                    "version": c.get("newest_version"),
                    "description": c.get("description", ""),
                    "homepage": c.get("homepage"),
                    "repository": c.get("repository"),
                    "downloads": c.get("downloads"),
                }
        except Exception as exc:
            logger.debug(
                "crates.io lookup failed for %r: %s",
                _redact_service_error(crate),
                _redact_service_error(exc),
            )
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
                payload = resp.json()
                response = (
                    payload.get("response") if isinstance(payload, dict) else None
                )
                raw_docs = (
                    response.get("docs", []) if isinstance(response, dict) else []
                )
                docs = _coerce_dict_list(raw_docs)
                if docs:
                    d = docs[0]
                    return {
                        "group_id": d.get("g"),
                        "artifact_id": d.get("a"),
                        "version": d.get("latestVersion"),
                        "timestamp": d.get("timestamp"),
                    }
        except Exception as exc:
            logger.debug(
                "Maven lookup failed for %s:%s: %s",
                _redact_service_error(group_id),
                _redact_service_error(artifact_id),
                _redact_service_error(exc),
            )
        return None

    async def get_packagist_info(self, package: str) -> dict | None:
        """Fetch PHP Composer package metadata from Packagist (unlimited)."""
        try:
            resp = await self._http.get(
                f"https://packagist.org/packages/{package}.json"
            )
            if resp.status_code == 200:
                payload = resp.json()
                pkg = payload.get("package") if isinstance(payload, dict) else None
                if not isinstance(pkg, dict):
                    return None
                raw_versions = pkg.get("versions", {})
                versions = raw_versions if isinstance(raw_versions, dict) else {}
                latest_key = next(
                    (
                        k
                        for k in versions
                        if isinstance(k, str) and not k.startswith("dev-")
                    ),
                    None,
                )
                latest = versions.get(latest_key, {}) if latest_key else {}
                if not isinstance(latest, dict):
                    latest = {}
                return {
                    "name": package,
                    "version": latest.get("version"),
                    "description": pkg.get("description", ""),
                    "homepage": latest.get("homepage"),
                }
        except Exception as exc:
            logger.debug(
                "Packagist lookup failed for %r: %s",
                _redact_service_error(package),
                _redact_service_error(exc),
            )
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
                payload = resp.json()
                vulns = payload.get("vulns", []) if isinstance(payload, dict) else []
                return _coerce_dict_list(vulns)
        except Exception as exc:
            logger.debug(
                "OSV check failed for %s@%s: %s",
                _redact_service_error(package),
                _redact_service_error(version),
                _redact_service_error(exc),
            )
        return []

    async def check_nvd(self, cve_id: str) -> dict | None:
        """Look up a CVE in the NVD/NIST database (free key, high limits)."""
        key = _coerce_non_empty_service_secret("NVD_API_KEY")
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
                payload = resp.json()
                raw_vulns = (
                    payload.get("vulnerabilities", [])
                    if isinstance(payload, dict)
                    else []
                )
                vulns = _coerce_dict_list(raw_vulns)
                if vulns:
                    cve = vulns[0].get("cve")
                    return cve if isinstance(cve, dict) else None
        except Exception as exc:
            logger.debug(
                "NVD lookup failed for %r: %s",
                _redact_service_error(cve_id),
                _redact_service_error(exc),
            )
        return None

    async def check_snyk(self, packages: list[dict]) -> list[dict]:
        """Check packages against Snyk vulnerability database (200 tests/month free).

        *packages* should be a list of ``{"name": ..., "version": ...}`` dicts.
        """
        key = _coerce_non_empty_service_secret("SNYK_API_KEY")
        if not key:
            return []
        try:
            resp = await self._http.post(
                "https://api.snyk.io/v1/test/pip",
                headers={"Authorization": f"token {key}"},
                json={"packages": packages},
            )
            if resp.status_code == 200:
                payload = resp.json()
                issues = payload.get("issues") if isinstance(payload, dict) else None
                raw_vulns = (
                    issues.get("vulnerabilities", [])
                    if isinstance(issues, dict)
                    else []
                )
                vulns = _coerce_dict_list(raw_vulns)
                return [
                    {
                        "package": v.get("package"),
                        "severity": v.get("severity"),
                        "title": v.get("title"),
                        "fix": v.get("fixedIn", "No fix"),
                    }
                    for v in vulns
                ]
        except Exception as exc:
            logger.debug(
                "Snyk check failed for %d packages: %s",
                len(packages),
                _redact_service_error(exc),
            )
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
        if isinstance(osv_vulns, BaseException) or not isinstance(osv_vulns, list):
            osv_vulns = []
        else:
            osv_vulns = _coerce_dict_list(osv_vulns)
        if isinstance(snyk_vulns, BaseException) or not isinstance(snyk_vulns, list):
            snyk_vulns = []
        else:
            snyk_vulns = _coerce_dict_list(snyk_vulns)

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
        ext = _semgrep_extension(language)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                prefix="codey_scan_",
                suffix=f".{ext}",
                delete=False,
            ) as f:
                tmp_path = f.name
                f.write(code)
            proc = await asyncio.create_subprocess_exec(
                "semgrep",
                "--config",
                "auto",
                "--json",
                "--quiet",
                "--metrics=off",
                "--disable-version-check",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                await _terminate_service_process(proc, "Semgrep")
                logger.warning("Semgrep scan timed out")
                return []

            payload = json.loads(_decode_process_output(stdout))
            findings = payload.get("results", []) if isinstance(payload, dict) else []
            return _normalize_semgrep_findings(findings)
        except FileNotFoundError:
            logger.debug("Semgrep not installed, skipping scan")
        except Exception as exc:
            logger.debug(
                "Semgrep scan failed: %s",
                _redact_service_error(exc),
            )
        finally:
            if tmp_path is not None:
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
        token = _coerce_non_empty_service_secret(
            "GITHUB_TOKEN"
        ) or _coerce_non_empty_service_secret("GITHUB_CLIENT_SECRET")
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
                payload = resp.json()
                raw_items = payload.get("items", []) if isinstance(payload, dict) else []
                results: list[dict] = []
                for item in _coerce_dict_list(raw_items):
                    repository = item.get("repository")
                    repo_name = (
                        repository.get("full_name")
                        if isinstance(repository, dict)
                        else None
                    )
                    path = item.get("path")
                    url = item.get("html_url")
                    if not all(isinstance(value, str) for value in (repo_name, path, url)):
                        continue
                    results.append({"repo": repo_name, "path": path, "url": url})
                return results
        except Exception as exc:
            logger.debug(
                "GitHub code search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
        return []

    async def search_github_repos(
        self,
        query: str,
        *,
        per_page: int = 5,
    ) -> list[dict]:
        """Search GitHub repositories."""
        token = _coerce_non_empty_service_secret(
            "GITHUB_TOKEN"
        ) or _coerce_non_empty_service_secret("GITHUB_CLIENT_SECRET")
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
                payload = resp.json()
                raw_items = payload.get("items", []) if isinstance(payload, dict) else []
                results: list[dict] = []
                for item in _coerce_dict_list(raw_items):
                    full_name = item.get("full_name")
                    url = item.get("html_url")
                    if not isinstance(full_name, str) or not isinstance(url, str):
                        continue
                    results.append(
                        {
                            "full_name": full_name,
                            "description": item.get("description", ""),
                            "stars": item.get("stargazers_count", 0),
                            "url": url,
                        }
                    )
                return results
        except Exception as exc:
            logger.debug(
                "GitHub repo search failed for %r: %s",
                _redact_service_error(query),
                _redact_service_error(exc),
            )
        return []

    # ------------------------------------------------------------------
    # DOCUMENTATION
    # ------------------------------------------------------------------

    async def fetch_devdocs(self, library: str) -> str | None:
        """Fetch documentation index from DevDocs (free, no key)."""
        try:
            library_path = _quote_service_path_segment(library)
            resp = await self._http.get(
                f"https://devdocs.io/api/entries/{library_path}"
            )
            if resp.status_code == 200:
                entries = _coerce_dict_list(resp.json())
                names = [
                    name
                    for entry in entries[:20]
                    if isinstance(name := entry.get("name"), str)
                ]
                if names:
                    return "\n".join(names)
        except Exception as exc:
            logger.debug(
                "DevDocs fetch failed for %r: %s",
                _redact_service_error(library),
                _redact_service_error(exc),
            )
        return None

    async def fetch_libraries_io(self, package: str, platform: str = "pypi") -> dict | None:
        """Cross-ecosystem dependency intelligence via Libraries.io (free key)."""
        key = _coerce_non_empty_service_secret("LIBRARIES_IO_API_KEY")
        if not key:
            return None
        try:
            platform_path = _quote_service_path_segment(platform)
            package_path = _quote_service_path_segment(package)
            resp = await self._http.get(
                f"https://libraries.io/api/{platform_path}/{package_path}",
                params={"api_key": key},
            )
            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, dict):
                    return None
                return {
                    "name": data.get("name"),
                    "platform": data.get("platform"),
                    "latest_version": data.get("latest_release_number"),
                    "dependents_count": data.get("dependents_count"),
                    "rank": data.get("rank"),
                    "homepage": data.get("homepage"),
                }
        except Exception as exc:
            logger.debug(
                "Libraries.io lookup failed for %s/%r: %s",
                _redact_service_error(platform),
                _redact_service_error(package),
                _redact_service_error(exc),
            )
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

        api_key = _coerce_non_empty_service_secret(cfg["key"])
        if not api_key:
            return None

        base = cfg["base"]
        # Handle Cloudflare account-id substitution
        if provider == "cloudflare":
            account_id = _coerce_non_empty_service_secret("CLOUDFLARE_ACCOUNT_ID")
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
                return _coerce_llm_content(resp.json())
        except Exception as exc:
            logger.debug(
                "LLM completion failed for %s/%s: %s",
                _redact_service_error(provider),
                _redact_service_error(model),
                _redact_service_error(exc),
            )
        return None

    # ------------------------------------------------------------------
    # NOTIFICATIONS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # CODE SECURITY — Additional Scanners
    # ------------------------------------------------------------------

    async def check_sonarcloud(
        self, project_key: str, *, metric_keys: str = "bugs,vulnerabilities,code_smells"
    ) -> dict | None:
        """Fetch code quality metrics from SonarCloud (free for public repos)."""
        token = _coerce_non_empty_service_secret("SONARCLOUD_TOKEN")
        if not token:
            return None
        try:
            resp = await self._http.get(
                "https://sonarcloud.io/api/measures/component",
                headers={"Authorization": f"Bearer {token}"},
                params={"component": project_key, "metricKeys": metric_keys},
            )
            if resp.status_code == 200:
                payload = resp.json()
                component = (
                    payload.get("component") if isinstance(payload, dict) else None
                )
                raw_measures = (
                    component.get("measures", [])
                    if isinstance(component, dict)
                    else []
                )
                metrics: dict = {}
                for measure in _coerce_dict_list(raw_measures):
                    metric = measure.get("metric")
                    if isinstance(metric, str) and "value" in measure:
                        metrics[metric] = measure["value"]
                return metrics
        except Exception as exc:
            logger.debug(
                "SonarCloud check failed for %r: %s",
                _redact_service_error(project_key),
                _redact_service_error(exc),
            )
        return None

    async def check_aikido(self, repo_url: str) -> list[dict] | None:
        """Fetch security findings from Aikido Security (free tier)."""
        key = _coerce_non_empty_service_secret("AIKIDO_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.get(
                "https://app.aikido.dev/api/v1/issues",
                headers={"Authorization": f"Bearer {key}"},
                params={"repo": repo_url, "status": "open"},
            )
            if resp.status_code == 200:
                payload = resp.json()
                issues = payload.get("issues", []) if isinstance(payload, dict) else []
                return _coerce_dict_list(issues)
        except Exception as exc:
            logger.debug(
                "Aikido check failed for %r: %s",
                _redact_service_error(repo_url),
                _redact_service_error(exc),
            )
        return None

    async def check_deepsource(self, repo: str) -> dict | None:
        """Fetch code analysis from DeepSource (free for public repos)."""
        token = _coerce_non_empty_service_secret("DEEPSOURCE_TOKEN")
        if not token:
            return None
        try:
            resp = await self._http.post(
                "https://api.deepsource.io/graphql",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "query": """
                        query($repo: String!) {
                            repository(login: $repo) {
                                activeIssueCount
                                resolvedIssueCount
                                issues(first: 10) {
                                    edges {
                                        node { title category shortcode }
                                    }
                                }
                            }
                        }
                    """,
                    "variables": {"repo": repo},
                },
            )
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                repository = data.get("repository") if isinstance(data, dict) else None
                return repository if isinstance(repository, dict) else None
        except Exception as exc:
            logger.debug(
                "DeepSource check failed for %r: %s",
                _redact_service_error(repo),
                _redact_service_error(exc),
            )
        return None

    # ------------------------------------------------------------------
    # MONITORING
    # ------------------------------------------------------------------

    async def betterstack_create_monitor(
        self, url: str, *, name: str = "Codey Monitor"
    ) -> dict | None:
        """Create an uptime monitor via BetterStack (3 monitors free)."""
        key = _coerce_non_empty_service_secret("BETTERSTACK_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://uptime.betterstack.com/api/v2/monitors",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "monitor_type": "status",
                    "url": url,
                    "pronounceable_name": name,
                    "check_frequency": 60,
                },
            )
            if resp.status_code in (200, 201):
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                attributes = data.get("attributes") if isinstance(data, dict) else None
                return attributes if isinstance(attributes, dict) else None
        except Exception as exc:
            logger.debug(
                "BetterStack monitor creation failed: %s",
                _redact_service_error(exc),
            )
        return None

    async def betterstack_get_monitors(self) -> list[dict]:
        """List all BetterStack uptime monitors."""
        key = _coerce_non_empty_service_secret("BETTERSTACK_API_KEY")
        if not key:
            return []
        try:
            resp = await self._http.get(
                "https://uptime.betterstack.com/api/v2/monitors",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code == 200:
                payload = resp.json()
                raw_monitors = (
                    payload.get("data", []) if isinstance(payload, dict) else []
                )
                monitors: list[dict] = []
                for monitor in _coerce_dict_list(raw_monitors):
                    attributes = monitor.get("attributes")
                    if not isinstance(attributes, dict) or "id" not in monitor:
                        continue
                    if "url" not in attributes or "status" not in attributes:
                        continue
                    monitors.append(
                        {
                            "id": monitor["id"],
                            "url": attributes["url"],
                            "status": attributes["status"],
                            "last_checked": attributes.get("last_checked_at"),
                        }
                    )
                return monitors
        except Exception as exc:
            logger.debug(
                "BetterStack monitor list failed: %s",
                _redact_service_error(exc),
            )
        return []

    async def uptimerobot_get_monitors(self) -> list[dict]:
        """List all UptimeRobot monitors (50 free)."""
        key = _coerce_non_empty_service_secret("UPTIMEROBOT_API_KEY")
        if not key:
            return []
        try:
            resp = await self._http.post(
                "https://api.uptimerobot.com/v2/getMonitors",
                json={"api_key": key, "format": "json"},
            )
            if resp.status_code == 200:
                payload = resp.json()
                raw_monitors = (
                    payload.get("monitors", []) if isinstance(payload, dict) else []
                )
                monitors: list[dict] = []
                for monitor in _coerce_dict_list(raw_monitors):
                    if not all(
                        key in monitor
                        for key in ("id", "friendly_name", "url", "status")
                    ):
                        continue
                    monitors.append(
                        {
                            "id": monitor["id"],
                            "name": monitor["friendly_name"],
                            "url": monitor["url"],
                            "status": monitor["status"],
                        }
                    )
                return monitors
        except Exception as exc:
            logger.debug(
                "UptimeRobot list failed: %s",
                _redact_service_error(exc),
            )
        return []

    async def uptimerobot_create_monitor(
        self, url: str, *, name: str = "Codey Monitor"
    ) -> dict | None:
        """Create an UptimeRobot HTTP monitor."""
        key = _coerce_non_empty_service_secret("UPTIMEROBOT_API_KEY")
        if not key:
            return None
        try:
            resp = await self._http.post(
                "https://api.uptimerobot.com/v2/newMonitor",
                json={
                    "api_key": key,
                    "format": "json",
                    "type": 1,  # HTTP(s)
                    "url": url,
                    "friendly_name": name,
                },
            )
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, dict) and payload.get("stat") == "ok":
                    monitor = payload.get("monitor")
                    return monitor if isinstance(monitor, dict) else None
        except Exception as exc:
            logger.debug(
                "UptimeRobot create failed: %s",
                _redact_service_error(exc),
            )
        return None

    # ------------------------------------------------------------------
    # DEV TOOLING
    # ------------------------------------------------------------------

    async def linear_get_issues(
        self, *, team_key: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Fetch issues from Linear (free tier)."""
        key = _coerce_non_empty_service_secret("LINEAR_API_KEY")
        if not key:
            return []
        try:
            query = """
                query($limit: Int!) {
                    issues(first: $limit, orderBy: updatedAt) {
                        nodes {
                            id identifier title state { name } priority
                            assignee { name } updatedAt
                        }
                    }
                }
            """
            resp = await self._http.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": key, "Content-Type": "application/json"},
                json={"query": query, "variables": {"limit": limit}},
            )
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                issues = data.get("issues") if isinstance(data, dict) else None
                raw_nodes = issues.get("nodes", []) if isinstance(issues, dict) else []
                result: list[dict] = []
                for node in _coerce_dict_list(raw_nodes):
                    identifier = node.get("identifier")
                    title = node.get("title")
                    if not isinstance(identifier, str) or not isinstance(title, str):
                        continue
                    state = node.get("state")
                    assignee = node.get("assignee")
                    result.append(
                        {
                            "id": identifier,
                            "title": title,
                            "state": state.get("name") if isinstance(state, dict) else None,
                            "priority": node.get("priority"),
                            "assignee": (
                                assignee.get("name")
                                if isinstance(assignee, dict)
                                else None
                            ),
                        }
                    )
                return result
        except Exception as exc:
            logger.debug(
                "Linear issues fetch failed for limit %s: %s",
                _redact_service_error(limit),
                _redact_service_error(exc),
            )
        return []

    async def vercel_get_deployments(self, *, limit: int = 5) -> list[dict]:
        """Fetch recent deployments from Vercel."""
        token = _coerce_non_empty_service_secret("VERCEL_TOKEN")
        if not token:
            return []
        try:
            resp = await self._http.get(
                "https://api.vercel.com/v6/deployments",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": limit},
            )
            if resp.status_code == 200:
                payload = resp.json()
                raw_deployments = (
                    payload.get("deployments", [])
                    if isinstance(payload, dict)
                    else []
                )
                deployments: list[dict] = []
                for deployment in _coerce_dict_list(raw_deployments):
                    deployment_id = deployment.get("uid")
                    if not isinstance(deployment_id, str) or not deployment_id:
                        continue
                    deployments.append(
                        {
                            "id": deployment_id,
                            "name": deployment.get("name"),
                            "state": deployment.get("state"),
                            "url": deployment.get("url"),
                            "created": deployment.get("created"),
                        }
                    )
                return deployments
        except Exception as exc:
            logger.debug(
                "Vercel deployments fetch failed: %s",
                _redact_service_error(exc),
            )
        return []

    async def railway_get_services(self, project_id: str) -> list[dict]:
        """Fetch services from a Railway project."""
        token = _coerce_non_empty_service_secret("RAILWAY_TOKEN")
        if not token:
            return []
        try:
            resp = await self._http.post(
                "https://backboard.railway.app/graphql/v2",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "query": """
                        query($projectId: String!) {
                            project(id: $projectId) {
                                services { edges { node {
                                    id name
                                }}}
                            }
                        }
                    """,
                    "variables": {"projectId": project_id},
                },
            )
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                project = data.get("project") if isinstance(data, dict) else None
                services = (
                    project.get("services") if isinstance(project, dict) else None
                )
                raw_edges = (
                    services.get("edges", []) if isinstance(services, dict) else []
                )
                result: list[dict] = []
                for edge in _coerce_dict_list(raw_edges):
                    node = edge.get("node")
                    if not isinstance(node, dict):
                        continue
                    service_id = node.get("id")
                    name = node.get("name")
                    if not isinstance(service_id, str) or not isinstance(name, str):
                        continue
                    result.append({"id": service_id, "name": name})
                return result
        except Exception as exc:
            logger.debug(
                "Railway services fetch failed: %s",
                _redact_service_error(exc),
            )
        return []

    # ------------------------------------------------------------------
    # COMMUNICATION — SMS
    # ------------------------------------------------------------------

    async def send_sms_twilio(self, to: str, body: str) -> bool:
        """Send SMS via Twilio (free trial credits)."""
        sid = _coerce_non_empty_service_secret("TWILIO_ACCOUNT_SID")
        token = _coerce_non_empty_service_secret("TWILIO_AUTH_TOKEN")
        from_number = _coerce_non_empty_service_secret("TWILIO_FROM_NUMBER")
        if not (sid and token and from_number):
            return False
        try:
            resp = await self._http.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={"To": to, "From": from_number, "Body": body},
            )
            return resp.status_code == 201
        except Exception as exc:
            logger.debug("Twilio SMS failed: %s", _redact_service_error(exc))
        return False

    # ------------------------------------------------------------------
    # NOTIFICATIONS
    # ------------------------------------------------------------------

    async def notify_discord(self, content: str) -> bool:
        """Send a message via Discord webhook (free)."""
        url = _coerce_service_webhook_url("DISCORD_WEBHOOK_URL")
        if not url:
            return False
        try:
            resp = await self._http.post(url, json={"content": content})
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.debug(
                "Discord notification failed: %s", _redact_service_error(exc)
            )
        return False

    async def notify_slack(self, text: str) -> bool:
        """Send a message via Slack incoming webhook (free)."""
        url = _coerce_service_webhook_url("SLACK_WEBHOOK_URL")
        if not url:
            return False
        try:
            resp = await self._http.post(url, json={"text": text})
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("Slack notification failed: %s", _redact_service_error(exc))
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
            if _coerce_non_empty_service_secret(cfg["key"])
        ]


# Module-level singleton — import and use directly.
intelligence_services = IntelligenceServices()
