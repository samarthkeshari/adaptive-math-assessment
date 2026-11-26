"""LLM provider selection, configured by environment variables.

LLM_PROVIDER   anthropic (default) | openai | gemini | deepseek | qwen | ollama | vllm | openai_compatible
LLM_MODEL      model id (required for every provider except anthropic)
LLM_BASE_URL   override the endpoint (required for openai_compatible)
LLM_API_KEY    override the API key (otherwise the provider's usual env var is used)
LLM_JSON_MODE  schema | object   override how JSON output is enforced
"""

import os

from .base import LLMError, LLMProvider

# base_url, API-key env var, JSON enforcement, token parameter name
PRESETS = {
    "openai":   (None, "OPENAI_API_KEY", "schema", "max_completion_tokens"),
    "gemini":   ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "schema", "max_tokens"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", "object", "max_tokens"),
    "qwen":     ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", "object", "max_tokens"),
    "ollama":   ("http://localhost:11434/v1", None, "schema", "max_tokens"),
    "vllm":     ("http://localhost:8000/v1", None, "schema", "max_tokens"),
    "openai_compatible": (None, None, "object", "max_tokens"),
}

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


def get_provider() -> LLMProvider:
    name = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    model = os.environ.get("LLM_MODEL", "").strip()
    api_key = os.environ.get("LLM_API_KEY") or None

    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model or DEFAULT_ANTHROPIC_MODEL, api_key)

    if name not in PRESETS:
        raise LLMError(f"Unknown LLM_PROVIDER '{name}'. Choose from: anthropic, {', '.join(PRESETS)}")
    if not model:
        raise LLMError(f"Set LLM_MODEL to the {name} model you want to use.")
    base_url, key_env, json_mode, token_param = PRESETS[name]
    base_url = os.environ.get("LLM_BASE_URL") or base_url
    if name == "openai_compatible" and not base_url:
        raise LLMError("Set LLM_BASE_URL for the openai_compatible provider.")
    api_key = api_key or (os.environ.get(key_env) if key_env else None)
    json_mode = os.environ.get("LLM_JSON_MODE", json_mode)

    from .openai_compatible import OpenAICompatibleProvider
    return OpenAICompatibleProvider(name, model, base_url, api_key, json_mode, token_param)


__all__ = ["LLMError", "LLMProvider", "get_provider", "PRESETS"]
