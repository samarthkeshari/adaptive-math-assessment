"""Any OpenAI-compatible Chat Completions endpoint.

Covers OpenAI, Google Gemini (OpenAI-compatible endpoint), DeepSeek, Qwen (DashScope
compatible mode), and self-hosted open-weight models (Gemma, Qwen, DeepSeek, Llama, ...)
served by Ollama, vLLM, LM Studio, etc.
"""

import openai

from .base import LLMError, LLMProvider, schema_instructions


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, name: str, model: str, base_url: str | None, api_key: str | None,
                 json_mode: str = "schema", token_param: str = "max_tokens"):
        """json_mode: 'schema' (response_format json_schema) or 'object' (json_object + schema in prompt).
        token_param: 'max_tokens' or 'max_completion_tokens' (OpenAI reasoning models)."""
        super().__init__(model)
        self.name = name
        self.json_mode = json_mode
        self.token_param = token_param
        # Local servers (Ollama, vLLM) ignore the key but the SDK requires a non-empty one.
        self.client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")

    async def _complete_json(self, system, prompt, schema, *, effort, max_tokens, attachments=None):
        user_content = prompt
        if attachments:
            if any(a["media_type"] == "application/pdf" for a in attachments):
                raise LLMError(f"{self.name} can't read PDF uploads here. Use Claude, upload an image "
                               "of the question, or paste its text.")
            # Vision-capable models take images as data URIs.
            user_content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": f"data:{a['media_type']};base64,{a['data']}"}}
                for a in attachments
            ]
        if self.json_mode == "schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
            system_text = system
        else:
            response_format = {"type": "json_object"}
            system_text = f"{system}\n\n{schema_instructions(schema)}"

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_content},
                ],
                response_format=response_format,
                **{self.token_param: min(max_tokens, 16000)},
            )
        except openai.AuthenticationError as e:
            raise LLMError(f"{self.name}: authentication failed. Check the API key.") from e
        except openai.RateLimitError as e:
            raise LLMError(f"{self.name}: rate limited. Please retry shortly.") from e
        except openai.APIStatusError as e:
            raise LLMError(f"{self.name} API error ({e.status_code}): {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"Could not reach the {self.name} endpoint.") from e

        if not response.choices:
            raise LLMError(f"{self.name} returned no choices.")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"{self.name} response was truncated. Please retry.")
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"{self.name} declined this request.")
        return choice.message.content or ""
