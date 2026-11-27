"""Claude via the native Anthropic SDK (structured outputs + adaptive thinking).

Credentials, first match wins:
  ANTHROPIC_API_KEY_HELPER   a command that prints a (short-lived) key, e.g. a company AI-gateway
                             token helper; re-run every ANTHROPIC_API_KEY_HELPER_TTL seconds
                             (default 300). Same idea as Claude Code's `apiKeyHelper` setting.
  LLM_API_KEY / ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile
ANTHROPIC_BASE_URL routes requests through a proxy/gateway (read by the SDK).
"""

import asyncio
import os
import subprocess
import time

import anthropic

from .base import LLMError, LLMProvider

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side refusal fallbacks (see Anthropic docs).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model)
        self.helper = os.environ.get("ANTHROPIC_API_KEY_HELPER", "").strip()
        self.helper_ttl = float(os.environ.get("ANTHROPIC_API_KEY_HELPER_TTL", "300"))
        self._helper_fetched_at = 0.0
        self._helper_lock = asyncio.Lock()
        self.use_fallbacks = model in _FALLBACK_MODELS
        if self.helper:
            self.client = None  # built lazily from the helper's key
        else:
            # No key → SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile.
            self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()

    def describe(self) -> dict:
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        host = base_url.split("://", 1)[-1].split("/", 1)[0] if base_url else ""
        connection = f"gateway ({host})" if host and "api.anthropic.com" not in host else "direct API"
        return {**super().describe(), "connection": connection}

    async def _get_client(self) -> anthropic.AsyncAnthropic:
        if not self.helper:
            return self.client
        async with self._helper_lock:
            if self.client is None or time.time() - self._helper_fetched_at > self.helper_ttl:
                key = await asyncio.to_thread(self._run_helper)
                # Sent as both x-api-key and Bearer, like Claude Code does with apiKeyHelper output.
                self.client = anthropic.AsyncAnthropic(
                    api_key=key, default_headers={"Authorization": f"Bearer {key}"})
                self._helper_fetched_at = time.time()
        return self.client

    def _run_helper(self) -> str:
        try:
            out = subprocess.run(os.path.expanduser(self.helper), shell=True, capture_output=True,
                                 text=True, timeout=60)
        except subprocess.TimeoutExpired as e:
            raise LLMError("ANTHROPIC_API_KEY_HELPER timed out.") from e
        key = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
        if out.returncode != 0 or not key:
            raise LLMError(f"ANTHROPIC_API_KEY_HELPER failed (exit {out.returncode}).")
        return key

    async def _complete_json(self, system, prompt, schema, *, effort, max_tokens, attachments=None):
        client = await self._get_client()
        content = prompt
        if attachments:  # images and PDFs go before the text, as content blocks
            content = [
                {"type": "document" if a["media_type"] == "application/pdf" else "image",
                 "source": {"type": "base64", "media_type": a["media_type"], "data": a["data"]}}
                for a in attachments
            ] + [{"type": "text", "text": prompt}]
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            try:
                response = await self._stream(client, kwargs, self.use_fallbacks)
            except anthropic.BadRequestError:
                if not self.use_fallbacks:
                    raise
                # Some proxies/gateways reject the fallback beta; continue without it.
                self.use_fallbacks = False
                response = await self._stream(client, kwargs, False)
        except anthropic.AuthenticationError as e:
            if self.helper:
                self.client = None  # force a fresh key next time
            raise LLMError("Anthropic authentication failed. Set ANTHROPIC_API_KEY "
                           "(or ANTHROPIC_API_KEY_HELPER for a gateway).") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the Anthropic API. Please retry shortly.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API.") from e

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request. Try rephrasing the topic.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model response was truncated. Please retry.")
        return next((b.text for b in response.content if b.type == "text"), "")

    async def _stream(self, client, kwargs: dict, with_fallbacks: bool):
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if with_fallbacks else {}
        # Streamed so large max_tokens never hits HTTP timeouts.
        async with client.beta.messages.stream(**kwargs, **extra) as stream:
            return await stream.get_final_message()
