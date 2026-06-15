from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light tests
    if exc.name != "openai":
        raise
    _OPENAI_IMPORT_ERROR: ModuleNotFoundError | None = exc
    AsyncOpenAI: Any = None
else:  # pragma: no cover - depends on optional runtime dependency
    _OPENAI_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

import contextvars as _contextvars

# Per-request BYOK override: holds {"provider","api_key","model"} or None.
_byok_override: "_contextvars.ContextVar" = _contextvars.ContextVar("codey_byok_override", default=None)


def set_byok_override(provider: object, api_key: object, model: object = None):
    """Set (or clear) the per-context BYOK override; returns a reset token."""
    prov = (str(provider).strip().lower() if provider else "")
    key = (str(api_key).strip() if api_key else "")
    if prov and key:
        mdl = (str(model).strip() if model else "")
        return _byok_override.set({"provider": prov, "api_key": key, "model": mdl or None})
    return _byok_override.set(None)


def clear_byok_override(token=None) -> None:
    try:
        if token is not None:
            _byok_override.reset(token)
        else:
            _byok_override.set(None)
    except Exception:
        _byok_override.set(None)


# Sensible default model per provider when a BYOK user does not specify one.
_BYOK_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "anthropic/claude-3.5-sonnet",
    "deepseek": "deepseek-chat",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "fireworks": "accounts/fireworks/models/llama-v3p3-70b-instruct",
    "cerebras": "llama-3.3-70b",
    "mistral": "mistral-large-latest",
    "gemini": "gemini-2.0-flash",
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


def _redact_provider_error(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL_RE.sub(r"\1***@", text)
    text = _URL_QUERY_SECRET_RE.sub(r"\1***", text)
    text = _NAMED_SECRET_RE.sub(r"\1***", text)
    return _EMAIL_ADDRESS_RE.sub(r"***@\1", text)

# ---------------------------------------------------------------------------
# Provider registry — base URLs and env var names for API keys
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "base": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "type": "anthropic",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
    },
    "openai": {
        "base": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
    },
    "mistral": {
        "base": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "together": {
        "base": "https://api.together.xyz/v1",
        "key_env": "TOGETHER_API_KEY",
    },
    "fireworks": {
        "base": "https://api.fireworks.ai/inference/v1",
        "key_env": "FIREWORKS_API_KEY",
    },
    "cloudflare": {
        "base": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "key_env": "CLOUDFLARE_API_KEY",
    },
    "cerebras": {
        "base": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
    },
    "huggingface": {
        "base": "https://api-inference.huggingface.co/v1",
        "key_env": "HUGGINGFACE_API_KEY",
    },
    "cohere": {
        "base": "https://api.cohere.ai/v1",
        "key_env": "COHERE_API_KEY",
    },
}

# ---------------------------------------------------------------------------
# Model routing table — maps task types to provider + model
# ---------------------------------------------------------------------------

MODELS: dict[str, dict[str, str]] = {
    # Groq (primary — fastest inference, 370 tok/sec, 1000 req/day free)
    "fast_code": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "code_generation": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "code_review": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "documentation": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "test_generation": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "debugging": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "default": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    # OpenRouter for specialized tasks (variety of models)
    "architecture": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "security_audit": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    "long_context": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
}

# Fallback models when primary is rate-limited (tried in order)
FALLBACK_MODELS: list[dict[str, str]] = [
    # Fallbacks (a different model/provider so a per-model daily cap is skipped)
    {"provider": "groq", "model": "llama-3.1-8b-instant"},
    # Paid but extremely cheap fallback (~$0.0001/request) — works when all free are throttled
    {"provider": "openrouter", "model": "deepseek/deepseek-chat"},
    {"provider": "openrouter", "model": "mistralai/mistral-small-3.1-24b-instruct"},
]

# ---------------------------------------------------------------------------
# Client cache — one AsyncOpenAI instance per provider
# ---------------------------------------------------------------------------

_client_cache: dict[str, AsyncOpenAI] = {}


def _require_openai_sdk() -> None:
    if _OPENAI_IMPORT_ERROR is not None:
        raise RuntimeError("openai is required for AI provider clients") from _OPENAI_IMPORT_ERROR


def _coerce_provider_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        for key in ("content", "text", "output", "code"):
            candidate = value.get(key)
            if candidate is None:
                continue
            normalized = _coerce_provider_text(candidate)
            if normalized:
                return normalized
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            candidate = _coerce_provider_text(item).strip()
            if candidate:
                parts.append(candidate)
        return "\n".join(parts)

    text_attr = getattr(value, "text", None)
    if text_attr is not None and text_attr is not value:
        return _coerce_provider_text(text_attr)

    content_attr = getattr(value, "content", None)
    if content_attr is not None and content_attr is not value:
        return _coerce_provider_text(content_attr)

    return ""


def _coerce_provider_choices(value: object) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_whitespace(value: str) -> bool:
    return any(char.isspace() for char in value)


def _coerce_non_empty_provider_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if _has_ascii_control(normalized) or _has_whitespace(normalized):
        return None
    return normalized or None


def get_client(provider: str) -> AsyncOpenAI:
    """Return a cached AsyncOpenAI-compatible client for *provider*.

    Raises ``ValueError`` if the provider is unknown and ``RuntimeError``
    if the required API key env var is not set.
    """
    _ov = _byok_override.get()
    if _ov and _ov.get("provider") == provider and _ov.get("api_key"):
        _cfg = PROVIDERS.get(provider)
        if _cfg is None:
            raise ValueError(f"Unknown provider: {provider}")
        _burl = _cfg["base"]
        if "{account_id}" in _burl:
            _burl = _burl.replace("{account_id}", _coerce_non_empty_provider_env("CLOUDFLARE_ACCOUNT_ID") or "")
        _require_openai_sdk()
        assert AsyncOpenAI is not None
        return AsyncOpenAI(api_key=_ov["api_key"], base_url=_burl)

    if provider in _client_cache:
        return _client_cache[provider]

    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise ValueError(f"Unknown provider: {provider}")

    key_env = cfg["key_env"]
    api_key = _coerce_non_empty_provider_env(key_env)
    if not api_key:
        raise RuntimeError(
            f"Provider '{provider}' requires env var {key_env} but it is not set"
        )

    base_url = cfg["base"]
    # Cloudflare requires account ID in the URL
    if "{account_id}" in base_url:
        account_id = _coerce_non_empty_provider_env("CLOUDFLARE_ACCOUNT_ID") or ""
        base_url = base_url.replace("{account_id}", account_id)

    _require_openai_sdk()
    assert AsyncOpenAI is not None
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    _client_cache[provider] = client
    return client


async def call_model(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    stream: bool = False,
    **kwargs: Any,
) -> str:
    """Call *model* via *provider* and return the assistant's text response.

    Supports all OpenAI-compatible providers. Automatically falls back
    to alternative models on 429 rate limit errors.

    Extra ``**kwargs`` are forwarded to ``chat.completions.create``.
    """
    try:
        return await _call_model_once(
            provider, model, messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=stream, **kwargs,
        )
    except Exception as e:
        if "429" in str(e) or "rate" in str(e).lower():
            logger.warning(
                "Primary model %s/%s rate-limited, trying fallbacks",
                _redact_provider_error(provider),
                _redact_provider_error(model),
            )
            for fb in FALLBACK_MODELS:
                await asyncio.sleep(2.0)
                try:
                    return await _call_model_once(
                        fb["provider"], fb["model"], messages,
                        temperature=temperature, max_tokens=max_tokens,
                        stream=stream, **kwargs,
                    )
                except Exception as fb_err:
                    logger.warning(
                        "Fallback %s/%s failed: %s",
                        _redact_provider_error(fb["provider"]),
                        _redact_provider_error(fb["model"]),
                        _redact_provider_error(fb_err),
                    )
                    continue
        raise


async def _call_model_once(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    stream: bool = False,
    **kwargs: Any,
) -> str:
    """Single attempt to call a model. Raises on failure."""
    client = get_client(provider)

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **kwargs,
    }

    if stream:
        chunks: list[str] = []
        async for chunk in await client.chat.completions.create(
            stream=True, **create_kwargs
        ):
            choices = _coerce_provider_choices(getattr(chunk, "choices", None))
            delta = getattr(choices[0], "delta", None) if choices else None
            content = getattr(delta, "content", None)
            if content:
                normalized = _coerce_provider_text(content)
                if normalized:
                    chunks.append(normalized)
        return "".join(chunks)

    response = await client.chat.completions.create(**create_kwargs)
    choices = _coerce_provider_choices(getattr(response, "choices", None))
    if not choices:
        raise RuntimeError("AI provider returned no choices")
    choice = choices[0]
    message = getattr(choice, "message", None)
    return _coerce_provider_text(getattr(message, "content", None))


def get_available_providers() -> dict[str, dict[str, str]]:
    """Return a dict of providers whose API key env vars are currently set."""
    available: dict[str, dict[str, str]] = {}
    for name, cfg in PROVIDERS.items():
        if _coerce_non_empty_provider_env(cfg["key_env"]):
            available[name] = cfg
        else:
            logger.debug("Provider '%s' skipped — %s not set", name, cfg["key_env"])
    return available


def get_available_models() -> dict[str, dict[str, str]]:
    """Return the subset of MODELS whose provider is available."""
    available_providers = get_available_providers()
    return {
        key: spec
        for key, spec in MODELS.items()
        if spec["provider"] in available_providers
    }


def resolve_model(task_key: str) -> tuple[str, str]:
    """Return ``(provider, model)`` for *task_key*, falling back to default.

    If the preferred provider is unavailable, tries the ``default`` entry.
    Raises ``RuntimeError`` when no providers are available at all.
    """
    _ov = _byok_override.get()
    if _ov and _ov.get("provider") and _ov.get("api_key"):
        _p = _ov["provider"]
        _base_spec = MODELS.get(task_key, MODELS["default"])
        if _ov.get("model"):
            return _p, _ov["model"]
        if _base_spec.get("provider") == _p:
            return _p, _base_spec["model"]
        return _p, _BYOK_DEFAULT_MODELS.get(_p, _base_spec["model"])

    spec = MODELS.get(task_key, MODELS["default"])
    provider_name = spec["provider"]

    # Check if the provider is available
    if _coerce_non_empty_provider_env(PROVIDERS[provider_name]["key_env"]):
        return provider_name, spec["model"]

    # Fall back to default
    default_spec = MODELS["default"]
    default_provider = default_spec["provider"]
    if _coerce_non_empty_provider_env(PROVIDERS[default_provider]["key_env"]):
        logger.warning(
            "Provider '%s' unavailable for task '%s', falling back to default (%s/%s)",
            provider_name,
            task_key,
            default_provider,
            default_spec["model"],
        )
        return default_provider, default_spec["model"]

    # Try any available provider
    available = get_available_providers()
    if available:
        fallback_name = next(iter(available))
        fallback_model = MODELS.get("default", MODELS["fast_code"])["model"]
        logger.warning(
            "Default provider unavailable, falling back to '%s'", fallback_name
        )
        return fallback_name, fallback_model

    raise RuntimeError("No AI providers available — set at least one API key env var")
