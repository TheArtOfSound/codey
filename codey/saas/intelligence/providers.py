from __future__ import annotations

import logging
import os
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

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
    # All routes use OpenRouter free models until other provider keys are added.
    # When Groq/Gemini/DeepSeek keys are set, resolve_model() will prefer them.
    "fast_code": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "code_generation": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "code_review": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "architecture": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "documentation": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "test_generation": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "debugging": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "security_audit": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "long_context": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "default": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
}

# Fallback models when primary is rate-limited
FALLBACK_MODELS: list[dict[str, str]] = [
    {"provider": "openrouter", "model": "mistralai/mistral-small-3.1-24b-instruct:free"},
    {"provider": "openrouter", "model": "google/gemma-3-27b-it:free"},
    {"provider": "openrouter", "model": "qwen/qwen3-32b:free"},
]

# ---------------------------------------------------------------------------
# Client cache — one AsyncOpenAI instance per provider
# ---------------------------------------------------------------------------

_client_cache: dict[str, AsyncOpenAI] = {}


def get_client(provider: str) -> AsyncOpenAI:
    """Return a cached AsyncOpenAI-compatible client for *provider*.

    Raises ``ValueError`` if the provider is unknown and ``RuntimeError``
    if the required API key env var is not set.
    """
    if provider in _client_cache:
        return _client_cache[provider]

    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise ValueError(f"Unknown provider: {provider}")

    key_env = cfg["key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"Provider '{provider}' requires env var {key_env} but it is not set"
        )

    base_url = cfg["base"]
    # Cloudflare requires account ID in the URL
    if "{account_id}" in base_url:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        base_url = base_url.replace("{account_id}", account_id)

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
            logger.warning("Primary model %s/%s rate-limited, trying fallbacks", provider, model)
            for fb in FALLBACK_MODELS:
                try:
                    return await _call_model_once(
                        fb["provider"], fb["model"], messages,
                        temperature=temperature, max_tokens=max_tokens,
                        stream=stream, **kwargs,
                    )
                except Exception as fb_err:
                    logger.warning("Fallback %s/%s failed: %s", fb["provider"], fb["model"], fb_err)
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
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                chunks.append(delta.content)
        return "".join(chunks)

    response = await client.chat.completions.create(**create_kwargs)
    choice = response.choices[0]
    return choice.message.content or ""


def get_available_providers() -> dict[str, dict[str, str]]:
    """Return a dict of providers whose API key env vars are currently set."""
    available: dict[str, dict[str, str]] = {}
    for name, cfg in PROVIDERS.items():
        if os.environ.get(cfg["key_env"]):
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
    spec = MODELS.get(task_key, MODELS["default"])
    provider_name = spec["provider"]

    # Check if the provider is available
    if os.environ.get(PROVIDERS[provider_name]["key_env"]):
        return provider_name, spec["model"]

    # Fall back to default
    default_spec = MODELS["default"]
    default_provider = default_spec["provider"]
    if os.environ.get(PROVIDERS[default_provider]["key_env"]):
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
