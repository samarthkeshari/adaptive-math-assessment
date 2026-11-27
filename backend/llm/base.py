"""Provider-agnostic LLM interface.

Every engine component asks for *structured JSON matching a schema*; providers differ only
in how they enforce it. Output is always validated here, whatever the provider promises,
and a malformed response is retried once with the validation error fed back.
"""

import json
import re
from abc import ABC, abstractmethod


class LLMError(Exception):
    """A provider failure that should be surfaced to the user."""


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def _complete_json(self, system: str, prompt: str, schema: dict, *,
                             effort: str, max_tokens: int, attachments: list[dict] | None = None) -> str:
        """Return the raw text of a response that should contain one JSON object.
        attachments: [{"media_type": "image/png" | ... | "application/pdf", "data": <base64>}]."""

    async def generate_json(self, system: str, prompt: str, schema: dict, *,
                            effort: str = "medium", max_tokens: int = 32000,
                            attachments: list[dict] | None = None) -> dict:
        """effort: 'low' | 'medium' | 'high'. Providers without an effort control ignore it.
        attachments: images/PDFs the model should read along with the prompt."""
        last_error = ""
        for attempt in range(2):
            p = prompt if attempt == 0 else (
                f"{prompt}\n\nYour previous reply was rejected: {last_error}\n"
                "Reply again with ONLY a JSON object that matches the schema exactly."
            )
            text = await self._complete_json(system, p, schema, effort=effort, max_tokens=max_tokens,
                                             attachments=attachments)
            try:
                data = extract_json(text)
                errors = validate(data, schema)
                if not errors:
                    return data
                last_error = "; ".join(errors[:5])
            except ValueError as e:
                last_error = str(e)
        raise LLMError(f"{self.name} returned invalid structured output: {last_error}")

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model}


def schema_instructions(schema: dict) -> str:
    """For providers without native schema enforcement: put the schema in the prompt."""
    return ("Respond with ONLY a single JSON object (no prose, no code fences) that conforms to "
            "this JSON Schema. Include every required field.\n" + json.dumps(schema))


def extract_json(text: str) -> dict:
    if not text or not text.strip():
        raise ValueError("empty response")
    # Reasoning models served via OpenAI-compatible APIs may emit <think>…</think> first.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in response")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse error: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "number": (int, float)}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-Schema check for the subset our schemas use."""
    t = schema.get("type")
    if t == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            return [f"{path}: expected integer"]
    elif t in _TYPES:
        if not isinstance(value, _TYPES[t]) or (t != "boolean" and isinstance(value, bool)):
            return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: must be one of {schema['enum']}"]
    errors = []
    if t == "object":
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
    elif t == "array" and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors
