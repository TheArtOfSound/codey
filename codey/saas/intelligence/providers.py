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
    "fast_code": {"provider": "groq", "model": "deepseek-r1-distill-qwen-32b"},
    "code_generation": {"provider": "deepseek", "model": "deepseek-coder"},
    "code_review": {"provider": "gemini", "model": "gemini-2.0-flash"},
    "architecture": {"provider": "groq", "model": "deepseek-r1-distill-llama-70b"},
    "documentation": {"provider": "gemini", "model": "gemini-2.0-flash-lite"},
    "test_generation": {
        "provider": "openrouter",
        "model": "qwen/qwen-2.5-coder-32b-instruct",
    },
    "debugging": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    "security_audit": {"provider": "deepseek", "model": "deepseek-reasoner"},
    "long_context": {"provider": "gemini", "model": "gemini-2.5-pro-preview-06-05"},
    "default": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
}

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

    Supports all OpenAI-compatible providers.  For the ``anthropic`` provider
    type, messages are sent through the OpenAI-compatible endpoint at
    ``/v1/messages`` which Anthropic exposes.

    Extra ``**kwargs`` are forwarded to ``chat.completions.create``.
    """
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
